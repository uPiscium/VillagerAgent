import json

import pytest

from benchmarks.cwah.matrix import MatrixRun, aggregate_results, build_matrix, matrix_port, parse_int_list, write_matrix_summary


def test_parse_int_list_and_build_matrix():
    assert parse_int_list("0, 2,3") == (0, 2, 3)
    assert build_matrix((0, 1), (3, 4)) == (
        MatrixRun(index=0, task_id=0, seed=3),
        MatrixRun(index=1, task_id=0, seed=4),
        MatrixRun(index=2, task_id=1, seed=3),
        MatrixRun(index=3, task_id=1, seed=4),
    )


def test_matrix_ports_are_unique_for_task_seed_collision_case():
    runs = build_matrix((0, 1), (0, 1))

    ports = [matrix_port(base_port=6314, run=run, port_stride=1) for run in runs]

    assert ports == [6314, 6315, 6316, 6317]
    assert len(ports) == len(set(ports))


def test_matrix_port_supports_stride_and_rejects_invalid_stride():
    run = MatrixRun(index=2, task_id=10, seed=20)

    assert matrix_port(base_port=7000, run=run, port_stride=10) == 7020
    with pytest.raises(ValueError, match="port_stride"):
        matrix_port(base_port=7000, run=run, port_stride=0)


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
        results=[{
            "task_id": 0,
            "seed": 1,
            "matrix_index": 0,
            "base_port": 6314,
            "passed": True,
            "metrics": {"task_success": False, "normalized_progress": 0.5, "episode_steps": 2},
            "event_counts": {"policy_overrides": 1},
            "diagnostics": {"failed_action_record_count": 2, "open_failure_record_count": 1, "navigation_loop_count": 1, "result_failure_count": 3, "failure_reason_counts": {"script_impossible": 2}, "open_failure_reason_counts": {"already_open": 1}},
        }],
    )

    summary = json.loads((tmp_path / "matrix_summary.json").read_text(encoding="utf-8"))
    metrics_csv = (tmp_path / "matrix_metrics.csv").read_text(encoding="utf-8")

    assert summary["aggregate"]["passed_runs"] == 1
    assert "matrix_index,task_id,seed,base_port,passed,task_success,normalized_progress,episode_steps,policy_overrides,failed_action_records,open_failure_records,navigation_loop_count,result_failures,failure_reason_counts,open_failure_reason_counts" in metrics_csv
    assert '0,0,1,6314,True,False,0.5,2,1,2,1,1,3,"{""script_impossible"": 2}","{""already_open"": 1}"' in metrics_csv
