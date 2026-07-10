from __future__ import annotations


def classify_failure_message(message: str) -> str:
    text = message.lower()
    if "not found source object" in text:
        return "not_found_source_object"
    if "not found object" in text:
        return "not_found_object"
    if "object already open" in text or "already open" in text:
        return "already_open"
    if "script is impossible to execute" in text:
        return "script_impossible"
    if "execution_general" in text:
        return "general_execution_failure"
    if "execution_failed" in text:
        return "execution_failed"
    if "bdbquit" in text:
        return "debugger_abort"
    return "unknown"


def failure_reason_counts_from_messages(messages: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        if not message:
            continue
        reason = classify_failure_message(message)
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def failure_reason_counts_from_process_output(output: str) -> dict[str, int]:
    messages = []
    for line in output.splitlines():
        normalized = line.strip()
        lowered = normalized.lower()
        if "'message':" in lowered or '"message":' in lowered or "assertionerror:" in lowered or "bdbquit" in lowered:
            messages.append(normalized)
    if not messages and "no success" in output.lower():
        messages.append(output)
    return failure_reason_counts_from_messages(messages)


def merge_count_dicts(*counts_by_reason: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for counts in counts_by_reason:
        for reason, count in counts.items():
            merged[reason] = merged.get(reason, 0) + int(count or 0)
    return dict(sorted((reason, count) for reason, count in merged.items() if count))
