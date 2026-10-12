"""``python -m scripts.example_media [--only NAME ...]``: record the examples' media (#213)."""

from __future__ import annotations

import argparse
import time

from . import scenes


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.example_media", description=__doc__)
    parser.add_argument("--only", nargs="+", metavar="NAME", help="record only these examples")
    parser.add_argument("--list", action="store_true", help="print each scene's caption and stop")
    args = parser.parse_args()

    known = scenes.by_name()
    chosen = args.only or list(known)
    unknown = sorted(set(chosen) - set(known))
    if unknown:
        parser.error(f"no scene for {', '.join(unknown)}; there are {', '.join(known)}")
    if args.list:
        for name in chosen:
            print(f"{name:<14} {known[name].caption}")
        return

    from .record import record_all

    started = time.monotonic()
    results = record_all([known[name] for name in chosen])
    total = sum(r.png + r.webp for r in results)
    print(f"{len(results)} examples in {time.monotonic() - started:.0f}s, committed media {total:,} bytes")


if __name__ == "__main__":
    main()
