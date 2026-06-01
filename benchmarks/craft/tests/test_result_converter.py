import json

from benchmarks.craft.result_converter import normalize_results


def test_result_converter_writes_normalized_files(tmp_path):
    config = {
        "run": {"name": "test", "seed": 3, "structures": [0], "turns": 1},
        "models": {"director": {"model": "d"}, "builder": {"model": "b"}},
        "villageragent": {"enabled": True},
    }
    normalize_results(
        config=config,
        condition="villageragent_directors",
        raw_result={"structure_id": 0, "turns": [], "final_progress": 0.0, "completed": False},
        output_dir=tmp_path,
    )
    assert (tmp_path / "normalized" / "summary.json").exists()
    assert (tmp_path / "normalized" / "turns.jsonl").exists()
    assert (tmp_path / "normalized" / "metrics.csv").exists()
    summary = json.loads((tmp_path / "normalized" / "summary.json").read_text())
    assert summary["benchmark"] == "CRAFT"
    assert summary["runtime"]["active_directors"] == ["D1", "D2", "D3"]
    assert summary["runtime"]["observed_fact_count"] == 0
    assert summary["runtime"]["reported_claim_count"] == 0


def test_result_converter_records_runtime_metrics(tmp_path):
    config = {
        "run": {"name": "test", "seed": 3, "structures": [0], "turns": 1},
        "models": {
            "director": {"model": "d", "provider": "ollama_native"},
            "builder": {"model": "b", "provider": "ollama_native"},
        },
        "villageragent": {"enabled": False},
    }
    normalize_results(
        config=config,
        condition="single_director_ablation",
        raw_result={
            "structure_id": 0,
            "turns": [{"builder_action": {"_builder_fallback": "fallback"}}],
            "final_progress": 0.0,
            "completed": False,
        },
        output_dir=tmp_path,
    )
    summary = json.loads((tmp_path / "normalized" / "summary.json").read_text())
    assert summary["providers"] == {"director": "ollama_native", "builder": "ollama_native"}
    assert summary["runtime"]["active_directors"] == ["D1"]
    assert summary["runtime"]["builder_fallback_count"] == 1
    assert summary["runtime"]["builder_fallback_rate"] == 1.0


def test_result_converter_writes_metrics_for_each_game(tmp_path):
    config = {
        "run": {"name": "test", "seed": 3, "structures": [0, 1], "turns": 1},
        "models": {"director": {"model": "d"}, "builder": {"model": "b"}},
        "villageragent": {"enabled": True},
    }
    normalize_results(
        config=config,
        condition="villageragent_directors",
        raw_result={
            "turns": [],
            "games": [
                {"structure_id": 0, "final_progress": 0.25, "completed": False},
                {"structure_id": 1, "final_progress": 0.75, "completed": True},
            ],
        },
        output_dir=tmp_path,
    )
    summary = json.loads((tmp_path / "normalized" / "summary.json").read_text())
    metrics_lines = (tmp_path / "normalized" / "metrics.csv").read_text().splitlines()
    assert summary["num_games"] == 2
    assert summary["mean_final_progress"] == 0.5
    assert summary["completion_rate"] == 0.5
    assert len(metrics_lines) == 3


def test_result_converter_counts_epistemic_metadata(tmp_path):
    config = {
        "run": {"name": "test", "seed": 3, "structures": [0], "turns": 1},
        "models": {"director": {"model": "d"}, "builder": {"model": "b"}},
        "villageragent": {"enabled": True},
    }
    normalize_results(
        config=config,
        condition="villageragent_directors",
        raw_result={
            "structure_id": 0,
            "turns": [{
                "director_metadata": {
                    "D1": {"epistemic": {"observed_facts": [{"node_id": "o1"}], "hypotheses": []}},
                    "D2": {"epistemic": {"observed_facts": [{"node_id": "o2"}], "hypotheses": [{"node_id": "h1"}]}},
                },
                "epistemic_claims": {"D1": {"node_id": "c1"}, "D2": {"node_id": "c2"}},
                "builder_action": {"action": "clarify"},
            }],
            "final_progress": 0.0,
            "completed": False,
        },
        output_dir=tmp_path,
    )
    summary = json.loads((tmp_path / "normalized" / "summary.json").read_text())
    metrics_text = (tmp_path / "normalized" / "metrics.csv").read_text()
    assert summary["runtime"]["observed_fact_count"] == 2
    assert summary["runtime"]["reported_claim_count"] == 2
    assert summary["runtime"]["hypothesis_count"] == 1
    assert "observed_fact_count" in metrics_text
