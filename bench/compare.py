"""Print two benchmark results side by side.

uv run python -m bench.compare bench/results/997ee59.json bench/results/current.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _flatten(results: dict) -> dict[str, float]:
    flat: dict[str, float] = {}
    for section in ("payload_bytes", "timing", "memory"):
        for key, value in results.get(section, {}).items():
            flat[f"{section}.{key}"] = value
    for section in ("ws", "ws_uvicorn", "ws_go"):
        for key, ws in results.get(section, {}).items():
            for metric in ("per_connection_kb", "joins_per_s", "events_per_s", "render_bytes"):
                flat[f"{section}.{key}.{metric}"] = ws[metric]
    return flat


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if value >= 100 or float(value).is_integer() else f"{value:.3f}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    base, current = (json.loads(Path(p).read_text()) for p in argv)
    a, b = _flatten(base), _flatten(current)
    label_a = base["meta"]["sha"] or "base"
    label_b = current["meta"]["sha"] or "current"
    print(f"{'metric':<40} {label_a:>12} {label_b:>12} {'change':>10}")
    for key in a:
        if key not in b:
            continue
        va, vb = a[key], b[key]
        change = "" if va == 0 else f"{(vb - va) / va * 100:+.0f}%"
        print(f"{key:<40} {_fmt(va):>12} {_fmt(vb):>12} {change:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
