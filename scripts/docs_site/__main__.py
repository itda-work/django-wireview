"""python -m scripts.docs_site build|serve (``make docs-site``, ``make docs-serve``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import DEFAULT_OUT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.docs_site", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build_cmd = commands.add_parser("build", help="build the site and check it (non-zero on any problem)")
    build_cmd.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: build/docs-site)")
    build_cmd.add_argument("--version", help="build as this version instead of pyproject.toml's")
    build_cmd.add_argument(
        "--update-urls", action="store_true", help="add the URLs the site now serves to docs/site-urls.txt"
    )

    serve_cmd = commands.add_parser("serve", help="build, serve, and rebuild when a source changes")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.add_argument("--out", type=Path, default=DEFAULT_OUT)
    serve_cmd.add_argument("--version", help="build as this version instead of pyproject.toml's")

    args = parser.parse_args(argv)
    if args.command == "serve":
        from .serve import serve

        return serve(port=args.port, out=args.out.resolve(), version=args.version)

    from .build import build

    result = build(out=args.out.resolve(), version=args.version, update_urls=args.update_urls)
    mode = "preview" if result.preview else "release"
    if result.problems:
        print(f"docs-site: {len(result.problems)} problem(s)", file=sys.stderr)
        for problem in result.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"docs-site: v{result.version} ({mode}), {len(result.files)} files in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
