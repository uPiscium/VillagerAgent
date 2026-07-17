from villageragent_visualizer.cli import build_parser


def test_cli_defaults_are_local_and_optional() -> None:
    args = build_parser().parse_args([])

    assert args.result_root == "result"
    assert args.host == "127.0.0.1"
    assert args.port == 8765


def test_cli_accepts_server_options() -> None:
    args = build_parser().parse_args([
        "--result-root",
        "artifacts",
        "--host",
        "0.0.0.0",
        "--port",
        "9000",
    ])

    assert args.result_root == "artifacts"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
