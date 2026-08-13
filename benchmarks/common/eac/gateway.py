"""Lowest supported in-process effect gateway for EAC actions."""
from __future__ import annotations

from typing import Any, Callable

from .authority import AuthorityError, RuntimeAuthority
from .model import ExactRequest, NativeEffectResult, PermitView, RejectionReason


class EffectRejected(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EffectGateway:
    """Owns a native effect callable so Authority callers cannot bypass fencing."""

    def __init__(self, authority: RuntimeAuthority, native_effect: Callable[[ExactRequest], Any],
                 *, env_pre: Callable[[ExactRequest], bool] | None = None,
                 sec_pre: Callable[[ExactRequest], bool] | None = None):
        if not callable(native_effect):
            raise TypeError("native effect callable required")
        self.__authority = authority
        self.__native = native_effect
        self.__env_pre = env_pre or (lambda request: True)
        self.__sec_pre = sec_pre or (lambda request: True)

    def execute(self, request: ExactRequest, permit: PermitView | str | None = None) -> Any:
        if self.__authority.mode != "authority":
            raise EffectRejected("authority_gateway_required")
        if permit is None:
            raise EffectRejected(RejectionReason.MISSING_PERMIT.value)
        try:
            token = self.__authority.validate_and_consume(request, permit)
            try:
                env_result = self.__env_pre(request)
            except BaseException:
                self.__authority.reject_pre_effect(token, "env_pre")
                raise
            if env_result is not True:
                self.__authority.reject_pre_effect(token, "env_pre")
                raise EffectRejected(RejectionReason.PRECHECK_REJECTED.value)
            try:
                sec_result = self.__sec_pre(request)
            except BaseException:
                self.__authority.reject_pre_effect(token, "sec_pre")
                raise
            if sec_result is not True:
                self.__authority.reject_pre_effect(token, "sec_pre")
                raise EffectRejected(RejectionReason.PRECHECK_REJECTED.value)
            return self.__authority.execute_fenced(token, request, self.__native)
        except AuthorityError as exc:
            raise EffectRejected(exc.reason) from exc

    def execute_advisory(self, request: ExactRequest) -> Any:
        if self.__authority.mode != "advisory":
            raise EffectRejected("advisory_gateway_required")
        shadow = self.__authority.shadow_permit(request.candidate_id)
        if shadow.request != request:
            raise EffectRejected(RejectionReason.MISMATCH.value)
        decision = self.__authority.evaluate(request.candidate_id)
        first_error = None
        try:
            env_result = "passed" if self.__env_pre(request) is True else "failed"
        except BaseException as exc:
            env_result, first_error = "failed", exc
        try:
            sec_result = "passed" if self.__sec_pre(request) is True else "failed"
        except BaseException as exc:
            sec_result = "failed"
            if first_error is None:
                first_error = exc
        if env_result != "passed" or sec_result != "passed":
            self.__authority.record_advisory(request, would_block=not decision.admissible,
                                             outcome="pre_effect_rejected",
                                             env_pre_result=env_result, sec_pre_result=sec_result,
                                             manifest_fingerprint=shadow.fingerprint)
            if first_error is not None:
                raise first_error
            raise EffectRejected(RejectionReason.PRECHECK_REJECTED.value)
        stale_shadow = not self.__authority.shadow_fresh(request, shadow)
        would_block = not decision.admissible or stale_shadow
        try:
            result = self.__native(request)
        except BaseException:
            self.__authority.record_advisory(request, would_block=would_block,
                                             outcome="effect_unknown", env_pre_result=env_result,
                                             sec_pre_result=sec_result,
                                             manifest_fingerprint=shadow.fingerprint)
            raise
        outcome = result.outcome if isinstance(result, NativeEffectResult) else "succeeded"
        self.__authority.record_advisory(request, would_block=would_block,
                                         outcome=outcome, env_pre_result=env_result,
                                         sec_pre_result=sec_result,
                                         manifest_fingerprint=shadow.fingerprint)
        return result.value if isinstance(result, NativeEffectResult) else result
