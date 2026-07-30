from __future__ import annotations


ITERATION_NOT_MEASURED = {
    "code": "iteration_not_measured",
    "message": "iteration usage was not measured by the external judger",
}
LEGACY_USAGE_AVAILABILITY_OMITTED = {
    "code": "legacy_payload_omitted_usage_availability",
    "message": "legacy payload omitted usage availability",
}


def build_iteration_metadata(
    *,
    source,
    limit,
    used,
    terminal_observations,
    usage_unavailable_reason=None,
    owner="external_meta_judger",
):
    reason = None if used is not None else (
        usage_unavailable_reason or dict(ITERATION_NOT_MEASURED)
    )
    record = {
        "available": any(
            value is not None
            for value in (source, limit, used, terminal_observations)
        ),
        "owner": owner,
        "source": source,
        "source_available": source is not None,
        "limit": limit,
        "limit_available": limit is not None,
        "used": used,
        "usage_available": used is not None,
        "usage_unavailable_reason": reason,
        "terminal_observations": terminal_observations,
        "terminal_observations_available": terminal_observations is not None,
    }
    errors = validate_iteration_availability(record)
    if errors:
        raise ValueError("; ".join(errors))
    return record


def normalize_iteration_metadata(iteration):
    payload = iteration if isinstance(iteration, dict) else {}
    reason = payload.get("usage_unavailable_reason")
    if payload.get("used") is None and reason is None:
        reason = (
            dict(LEGACY_USAGE_AVAILABILITY_OMITTED)
            if payload
            else dict(ITERATION_NOT_MEASURED)
        )
    return build_iteration_metadata(
        source=payload.get("source"),
        limit=payload.get("limit"),
        used=payload.get("used"),
        terminal_observations=payload.get("terminal_observations"),
        usage_unavailable_reason=reason,
        owner=payload.get("owner", "external_meta_judger"),
    )


def validate_iteration_availability(record):
    errors = []
    if record.get("usage_available") is True and record.get("used") is None:
        errors.append("usage_available=true requires used")
    if record.get("usage_available") is False:
        if record.get("used") is not None:
            errors.append("usage_available=false requires used=null")
        if not record.get("usage_unavailable_reason"):
            errors.append(
                "usage_available=false requires usage_unavailable_reason"
            )
    if record.get("limit_available") is True and record.get("limit") is None:
        errors.append("limit_available=true requires limit")
    if record.get("source_available") is True and record.get("source") is None:
        errors.append("source_available=true requires source")
    if (
        record.get("terminal_observations_available") is True
        and record.get("terminal_observations") is None
    ):
        errors.append(
            "terminal_observations_available=true requires terminal_observations"
        )
    return errors
