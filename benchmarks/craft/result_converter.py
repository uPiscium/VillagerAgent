import csv
import json
from pathlib import Path


def normalize_results(*, config: dict, condition: str, raw_result: dict, output_dir: Path) -> None:
    normalized_dir = output_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)

    turns = raw_result.get("turns", [])
    games = raw_result.get("games") or [raw_result]
    final_progress_values = [game.get("final_progress", 0.0) for game in games]
    mean_final_progress = (
        sum(final_progress_values) / len(final_progress_values)
        if final_progress_values else 0.0
    )
    completion_rate = (
        sum(1 for game in games if game.get("completed", False)) / len(games)
        if games else 0.0
    )
    builder_fallback_count = sum(
        1 for turn in turns if (turn.get("builder_action") or {}).get("_builder_fallback")
    )
    active_directors = _active_directors(config, condition)
    epistemic_counts = _epistemic_counts(turns)
    summary = {
        "benchmark": "CRAFT",
        "condition": condition,
        "run_name": config["run"]["name"],
        "seed": config["run"].get("seed"),
        "structures": config["run"].get("structures"),
        "turns": config["run"].get("turns"),
        "num_games": len(games),
        "mean_final_progress": mean_final_progress,
        "completion_rate": completion_rate,
        "models": {
            "director": config["models"]["director"]["model"],
            "builder": config["models"]["builder"]["model"],
        },
        "providers": {
            "director": config["models"]["director"].get("provider", ""),
            "builder": config["models"]["builder"].get("provider", ""),
        },
        "runtime": {
            "active_directors": active_directors,
            "active_director_count": len(active_directors),
            "builder_fallback_count": builder_fallback_count,
            "builder_fallback_rate": builder_fallback_count / len(turns) if turns else 0.0,
            "baseline_type": _baseline_type(condition),
            **epistemic_counts,
        },
        "villageragent": {
            "enabled": config.get("villageragent", {}).get("enabled", False),
            "use_task_decomposer": config.get("villageragent", {}).get("use_task_decomposer", False),
            "use_agent_controller": config.get("villageragent", {}).get("use_agent_controller", False),
            "use_state_manager": config.get("villageragent", {}).get("use_state_manager", False),
        },
        "partial_information": {
            "target_structure_exposed": config.get("villageragent", {}).get("expose_target_structure", False),
            "oracle_moves_exposed": config.get("villageragent", {}).get("expose_oracle_moves", False),
            "private_views_shared_raw": config.get("villageragent", {}).get("expose_private_views_to_global_state", False),
        },
    }
    with (normalized_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with (normalized_dir / "turns.jsonl").open("w", encoding="utf-8") as f:
        for turn in turns:
            f.write(json.dumps(turn, ensure_ascii=False) + "\n")

    metrics_path = normalized_dir / "metrics.csv"
    fieldnames = [
        "run_name",
        "condition",
        "structure_id",
        "seed",
        "turns",
        "completed",
        "final_progress",
        "completion_rate",
        "director_model",
        "builder_model",
        "director_provider",
        "builder_provider",
        "active_directors",
        "active_director_count",
        "builder_fallback_count",
        "builder_fallback_rate",
        "observed_fact_count",
        "reported_claim_count",
        "hypothesis_count",
        "baseline_type",
        "use_task_decomposer",
        "use_agent_controller",
        "use_state_manager",
        "leakage_passed",
    ]
    with metrics_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for game in games:
            game_epistemic_counts = _epistemic_counts(game.get("turns", []))
            writer.writerow({
                "run_name": config["run"]["name"],
                "condition": condition,
                "structure_id": game.get("structure_id"),
                "seed": config["run"].get("seed"),
                "turns": config["run"].get("turns"),
                "completed": game.get("completed", False),
                "final_progress": game.get("final_progress", 0.0),
                "completion_rate": 1.0 if game.get("completed", False) else 0.0,
                "director_model": config["models"]["director"]["model"],
                "builder_model": config["models"]["builder"]["model"],
                "director_provider": config["models"]["director"].get("provider", ""),
                "builder_provider": config["models"]["builder"].get("provider", ""),
                "active_directors": ",".join(active_directors),
                "active_director_count": len(active_directors),
                "builder_fallback_count": sum(
                    1 for turn in game.get("turns", [])
                    if (turn.get("builder_action") or {}).get("_builder_fallback")
                ),
                "builder_fallback_rate": _fallback_rate(game.get("turns", [])),
                "observed_fact_count": game_epistemic_counts["observed_fact_count"],
                "reported_claim_count": game_epistemic_counts["reported_claim_count"],
                "hypothesis_count": game_epistemic_counts["hypothesis_count"],
                "baseline_type": _baseline_type(condition),
                "use_task_decomposer": config.get("villageragent", {}).get("use_task_decomposer", False),
                "use_agent_controller": config.get("villageragent", {}).get("use_agent_controller", False),
                "use_state_manager": config.get("villageragent", {}).get("use_state_manager", False),
                "leakage_passed": game.get("leakage_passed", raw_result.get("leakage_passed", True)),
            })

    leakage_report = raw_result.get("leakage_report", {"checks": []})
    with (normalized_dir / "leakage_report.json").open("w", encoding="utf-8") as f:
        json.dump(leakage_report, f, ensure_ascii=False, indent=2)


def _active_directors(config: dict, condition: str) -> list[str]:
    if condition == "single_director_ablation":
        return ["D1"]
    villageragent = config.get("villageragent", {})
    if villageragent.get("enabled", False):
        return list(
            villageragent.get("active_director_ids")
            or villageragent.get("director_ids", ["D1", "D2", "D3"])
        )
    return []


def _baseline_type(condition: str) -> str:
    if condition == "official_baseline":
        return "comparable_artifact"
    return ""


def _fallback_rate(turns: list[dict]) -> float:
    if not turns:
        return 0.0
    fallback_count = sum(
        1 for turn in turns if (turn.get("builder_action") or {}).get("_builder_fallback")
    )
    return fallback_count / len(turns)


def _epistemic_counts(turns: list[dict]) -> dict:
    observed_fact_count = 0
    hypothesis_count = 0
    reported_claim_count = 0
    for turn in turns:
        reported_claim_count += len(turn.get("epistemic_claims", {}))
        for metadata in turn.get("director_metadata", {}).values():
            epistemic = metadata.get("epistemic", {}) if isinstance(metadata, dict) else {}
            observed_fact_count += len(epistemic.get("observed_facts", []))
            hypothesis_count += len(epistemic.get("hypotheses", []))
    return {
        "observed_fact_count": observed_fact_count,
        "reported_claim_count": reported_claim_count,
        "hypothesis_count": hypothesis_count,
    }
