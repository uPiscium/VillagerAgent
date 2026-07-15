import json
import sys

from benchmarks.craft.craft_env_adapter import _official_runner_command
from benchmarks.craft.external_runner_bootstrap import run_seeded


def test_seeded_bootstrap_repeats_python_and_numpy_order(tmp_path):
    (tmp_path / "sibling.py").write_text("VALUE = 'sibling-import-ok'\n", encoding="utf-8")
    script = tmp_path / "sample.py"
    script.write_text(
        "import json,random,sys\n"
        "from sibling import VALUE\n"
        "import numpy\n"
        "open(sys.argv[1], 'w').write(json.dumps({"
        "'argv': sys.argv, 'random': [random.random() for _ in range(3)], "
        "'numpy': numpy.random.random(3).tolist(), 'sibling': VALUE}))\n",
        encoding="utf-8",
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    run_seeded(17, str(script), [str(first), "structure-0"])
    run_seeded(17, str(script), [str(second), "structure-0"])
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    second_payload = json.loads(second.read_text(encoding="utf-8"))

    assert first_payload["random"] == second_payload["random"]
    assert first_payload["numpy"] == second_payload["numpy"]
    assert first_payload["sibling"] == "sibling-import-ok"
    assert first_payload["argv"][0] == str(script)
    assert first_payload["argv"][2] == "structure-0"


def test_official_runner_command_is_repeatable_and_preserves_upstream_argv(tmp_path):
    craft_repo = tmp_path / "CRAFT"
    config = {
        "official_runner_interpreter": sys.executable,
        "official_runner_bootstrap": "/repo/external_runner_bootstrap.py",
        "official_runner_mode": "api",
        "use_oracle": True,
        "oracle_n": 5,
        "builder_tool_use": False,
    }
    arguments = {
        "craft_repo": craft_repo,
        "dataset_path": tmp_path / "dataset.json",
        "output_dir": tmp_path / "output",
        "structures": [2, 0],
        "turns": 20,
        "seed": 17,
        "craft_config": config,
        "model_config": {
            "director": {"model": "gemma4:12b"},
            "builder": {"model": "gemma4:12b"},
        },
    }

    first = _official_runner_command(**arguments)
    second = _official_runner_command(**arguments)

    assert first == second
    assert first[:4] == [
        sys.executable,
        "/repo/external_runner_bootstrap.py",
        "17",
        str(craft_repo / "run_craft.py"),
    ]
    assert first[first.index("--structures") + 1] == "2,0"
