import argparse
from collections.abc import Sequence

import uvicorn

from villageragent_visualizer.app import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the VillagerAgent experiment visualizer API.")
    parser.add_argument("--result-root", default="result", help="Directory containing experiment runs.")
    parser.add_argument("--host", default="127.0.0.1", help="Address to bind the API server to.")
    parser.add_argument("--port", default=8765, type=int, help="Port to bind the API server to.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    uvicorn.run(create_app(result_root=args.result_root), host=args.host, port=args.port)
