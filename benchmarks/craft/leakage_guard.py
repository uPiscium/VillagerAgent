import json
from pathlib import Path
from typing import Any

from benchmarks.common.visibility import source_visibility_violations


class PartialInformationLeakageError(RuntimeError):
    """Raised when hidden CRAFT information appears in a prompt."""


class LeakageGuard:
    def __init__(self, config: dict):
        self.config = config
        self.reports: list[dict] = []

    def inspect_prompt(
        self,
        *,
        director_id: str,
        prompt_messages: list[dict],
        forbidden_payloads: dict,
        artifact_path: Path | None = None,
        included_source_ids: tuple[str, ...] | list[str] | None = None,
        source_visibility: dict | None = None,
    ) -> dict:
        prompt_text = "\n".join(m.get("content", "") for m in prompt_messages)
        violations = []
        for label, payload in forbidden_payloads.items():
            for needle in _payload_needles(payload):
                if needle and needle in prompt_text:
                    violations.append({"label": label, "match": needle[:200]})
                    break
        if included_source_ids is not None or source_visibility is not None:
            violations.extend(source_visibility_violations(
                agent_id=director_id,
                included_source_ids=tuple(included_source_ids or ()),
                source_visibility=source_visibility or {},
            ))

        report = {
            "director_id": director_id,
            "passed": not violations,
            "violations": violations,
        }
        if included_source_ids is not None:
            report["included_source_ids"] = list(included_source_ids)
        if artifact_path is not None:
            report["artifact_path"] = str(artifact_path)
        self.reports.append(report)
        if violations:
            raise PartialInformationLeakageError(
                f"Partial-information leakage detected for {director_id}: {violations}"
            )
        return report

    def inspect_prompt_artifact(
        self,
        *,
        artifact_path: Path,
        forbidden_payloads: dict,
        source_visibility: dict | None = None,
    ) -> dict:
        with artifact_path.open("r", encoding="utf-8") as f:
            artifact = json.load(f)
        return self.inspect_prompt(
            director_id=artifact.get("director_id", artifact_path.stem),
            prompt_messages=artifact.get("prompt_messages", []),
            forbidden_payloads=forbidden_payloads,
            artifact_path=artifact_path,
            included_source_ids=artifact.get("included_source_ids"),
            source_visibility=source_visibility or artifact.get("source_visibility"),
        )

    def save_report(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({"checks": self.reports}, f, ensure_ascii=False, indent=2)


def _payload_needles(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload] if len(payload) >= 6 else []
    if isinstance(payload, (int, float, bool)):
        return []
    if isinstance(payload, dict):
        compact = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        pretty = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)
        nested = [needle for value in payload.values() for needle in _nested_payload_needles(value)]
        return ([compact, pretty] if len(compact) >= 6 else []) + nested
    if isinstance(payload, list):
        compact = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        pretty = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)
        nested = [needle for value in payload for needle in _nested_payload_needles(value)]
        return ([compact, pretty] if len(compact) >= 6 else []) + nested
    return [str(payload)] if len(str(payload)) >= 6 else []


def _nested_payload_needles(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload] if len(payload) >= 12 else []
    if isinstance(payload, dict):
        return [needle for value in payload.values() for needle in _nested_payload_needles(value)]
    if isinstance(payload, list):
        return [needle for value in payload for needle in _nested_payload_needles(value)]
    return []
