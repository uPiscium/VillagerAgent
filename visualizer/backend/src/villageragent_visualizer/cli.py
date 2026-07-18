import argparse
from collections.abc import Sequence

import uvicorn

from villageragent_visualizer.app import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the VillagerAgent experiment visualizer API.")
    parser.add_argument("--result-root", default="result", help="Directory containing experiment runs.")
    parser.add_argument("--host", default="127.0.0.1", help="Address to bind the API server to.")
    parser.add_argument("--port", default=8765, type=int, help="Port to bind the API server to.")
    parser.add_argument("--frontend-dist", help="Optional built frontend directory to serve with SPA fallback.")
    parser.add_argument("--world-view-url", help="Optional read-only viewer URL, including its explicit port.")
    parser.add_argument("--allow-remote-world-view", action="store_true", help="Explicitly allow a non-loopback viewer URL.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    uvicorn.run(create_app(result_root=args.result_root, frontend_dist=args.frontend_dist, world_view_url=args.world_view_url, allow_remote_world_view=args.allow_remote_world_view), host=args.host, port=args.port)
