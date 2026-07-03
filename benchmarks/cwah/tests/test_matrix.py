import json

from benchmarks.cwah.matrix import MatrixRun, aggregate_results, build_matrix, parse_int_list, write_matrix_summary


def test_parse_int_list_and_build_matrix():
    assert parse_int_list("0, 2,3") == (0, 2, 3)
    assert build_matrix((0, 1), (3, 4)) == (
        MatrixRun(task_id=0, seed=3),
        MatrixRun(task_id=0, seed=4),
        MatrixRun(task_id=1, seed=3),
        MatrixRun(task_id=1, seed=4),
    )


def test_aggregate_results_counts_passes_and_progress():
    aggregate = aggregate_results([
        {"passed": True, "metrics": {"task_success": True, "normalized_progress": 1.0}},
        {"passed": False, "metrics": {"task_success": False, "normalized_progress": 0.25}},
    ])

    assert aggregate == {
        "runs": 2,
        "passed_runs": 1,
        "failed_runs": 1,
        "task_successes": 1,
        "average_progress": 0.625,
    }


def test_write_matrix_summary(tmp_path):
    write_matrix_summary(
        output_dir=tmp_path,
        results=[{"task_id": 0, "seed": 1, "passed": True, "metrics": {"task_success": False, "normalized_progress": 0.5, "episode_steps": 2}}],
    )

    summary = json.loads((tmp_path / "matrix_summary.json").read_text(encoding="utf-8"))
    metrics_csv = (tmp_path / "matrix_metrics.csv").read_text(encoding="utf-8")

    assert summary["aggregate"]["passed_runs"] == 1
    assert "task_id,seed,passed,task_success,normalized_progress,episode_steps" in metrics_csv
    assert "0,1,True,False,0.5,2" in metrics_csv
