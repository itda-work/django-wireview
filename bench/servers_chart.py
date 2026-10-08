"""The ASGI server comparison docs/DEPLOYMENT.md shows, from the bench.servers results (#191).

    uv run python -m bench.servers_chart             (make bench-servers-charts) the SVG charts
    uv run python -m bench.servers_chart --table     the tables, one per machine
    uv run python -m bench.servers_chart --facts     the numbers the prose quotes

``MACHINES`` names the results per machine. After a new ``make bench-servers`` (or a run of
bench/windows/ssh.sh), point it at the new files and run this again: tests/test_bench_servers.py
fails until the document says what the files say.

Standard library only, and the same bytes for the same results. The drawing is
bench/compare_fastapi/chart.py's, with four series: the first four slots of the same
validated categorical palette.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from bench.compare_fastapi.chart import Group, Panel, ms, render

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"
IMAGES = ROOT / "docs" / "images"

SERIES = ("daphne", "uvicorn", "uvicorn-nodeflate", "granian")
SERIES_LABELS = {
    "daphne": "daphne",
    "uvicorn": "uvicorn",
    "uvicorn-nodeflate": "uvicorn 압축 끔",
    "granian": "Granian",
}
STYLE = """
.bg{fill:#fcfcfb}.t1{fill:#0b0b0b}.t2{fill:#52514e}.t3{fill:#898781}
.grid{stroke:#e1e0d9}.base{stroke:#c3c2b7}
.s0{fill:#2a78d6}.s1{fill:#eb6834}.s2{fill:#1baf7a}.s3{fill:#eda100}
@media (prefers-color-scheme:dark){
.bg{fill:#1a1a19}.t1{fill:#ffffff}.t2{fill:#c3c2b7}.t3{fill:#898781}
.grid{stroke:#2c2c2a}.base{stroke:#383835}
.s0{fill:#3987e5}.s1{fill:#d95926}.s2{fill:#199e70}.s3{fill:#c98500}
}
text{font-family:system-ui,-apple-system,"Segoe UI","Apple SD Gothic Neo","Noto Sans KR",sans-serif}
"""


@dataclass(frozen=True)
class Machine:
    label: str  # the file-name label and the chart's name
    title: str  # how the document names the machine
    files: tuple[str, ...]  # bench/results names, in the order the table lists them


MACHINES = (
    Machine(
        "macos",
        "macOS (Apple M5 Pro)",
        ("606e407-servers-macos.json", "6e27122-servers-macos-nats-4proc.json"),
    ),
    Machine(
        "win10",
        "Windows 10 실기 (Intel i7-7567U, 2코어 4스레드)",
        (
            "51e1456-servers-win10-400.json",
            "2b213a8-servers-win10-daphne-600.json",
            "51e1456-servers-win10.json",
            "6e27122-servers-win10-nats-4proc.json",
        ),
    ),
)

#: bench/servers_shutdown.py's result: what SIGTERM does to open connections and leaving()
SHUTDOWN = "2b213a8-servers-shutdown-macos.json"

METRICS = ("per_connection_kb", "joins_per_s", "events_per_s", "broadcast_ms")
JOINED = re.compile(r"joined (\d+) of (\d+) connections")


def load(name: str) -> dict:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


@dataclass
class Cell:
    """One server at one setting: medians over the rounds that finished, and those that did not."""

    median: dict[str, float]
    low: dict[str, float]
    high: dict[str, float]
    rounds: int
    failed: list[str]  # each failed round's error


def setting(result: dict) -> str:
    s = result["settings"]
    layer = "InMemory" if s["layer"] == "memory" else s["layer"].upper()
    return f"{layer} {s['processes']}프로세스 · {s['connections']:,}연결"


def short(result: dict) -> str:
    """The chart's group label: the label column is narrow."""
    s = result["settings"]
    procs = f"{s['layer'].upper()} ×{s['processes']}" if s["layer"] != "memory" else "1프로세스"
    return f"{procs} {s['connections']:,}연결"


def cells(result: dict) -> dict[tuple[int, str], Cell]:
    """(items, server) -> Cell for every server and component size of one result."""
    out: dict[tuple[int, str], Cell] = {}
    for server, data in result["servers"].items():
        for items in result["settings"]["items"]:
            sized = [r[f"items_{items}"] for r in data["rounds"] if f"items_{items}" in r]
            done = [r for r in sized if "error" not in r]
            failed = [r["error"] for r in sized if "error" in r]
            if not done and not failed:
                continue
            values = {m: [r[m] for r in done] for m in METRICS}
            out[(items, server)] = Cell(
                median={m: statistics.median(v) for m, v in values.items() if v},
                low={m: min(v) for m, v in values.items() if v},
                high={m: max(v) for m, v in values.items() if v},
                rounds=len(done),
                failed=failed,
            )
    return out


def _death(error: str) -> str:
    joined = JOINED.search(error)
    return f"{int(joined.group(1)):,}/{int(joined.group(2)):,}연결에서 죽음" if joined else "실패"


def _kb(value: float) -> str:
    return f"{value:,.1f} KB"


def _rate(value: float) -> str:
    return f"{value:,.0f}/s"


FORMAT = {"per_connection_kb": _kb, "joins_per_s": _rate, "events_per_s": _rate, "broadcast_ms": ms}


def _value(cell: Cell, metric: str) -> str:
    fmt = FORMAT[metric]
    text = fmt(cell.median[metric])
    if cell.rounds > 1:
        low = fmt(cell.low[metric])
        for unit in (" ms", " KB", "/s"):
            low = low.removesuffix(unit)
        text += f" ({low}~{fmt(cell.high[metric])})"
    return text


# -- the tables ----------------------------------------------------------------------------


def table(machine: Machine) -> str:
    """Every server at every setting: the median over rounds, the rounds' range in parentheses."""
    header = "| 설정 | 서버 | 연결당 메모리 | join | 이벤트 | 브로드캐스트 | 회차 |"
    rows = [header, "|------|------|---:|---:|---:|---:|---:|"]
    for name in machine.files:
        result = load(name)
        by = cells(result)
        for items in result["settings"]["items"]:
            for server in SERIES:
                cell = by.get((items, server))
                if cell is None:
                    continue
                if cell.rounds:
                    values = [_value(cell, m) for m in METRICS]
                    count = f"{cell.rounds}" + (f" (실패 {len(cell.failed)})" if cell.failed else "")
                else:
                    values = [_death(cell.failed[0]), "—", "—", "—"]
                    count = f"0 (실패 {len(cell.failed)})"
                rows.append(
                    f"| {setting(result)} · 항목 {items} | {SERIES_LABELS[server]} | "
                    + " | ".join(values)
                    + f" | {count} |"
                )
    return "\n".join(rows)


def shutdown_table() -> str:
    """SIGTERM with joined components whose leaving() takes a while, per server."""
    result = load(SHUTDOWN)
    rows = [
        "| 서버 | 종료 옵션 | 클라이언트가 받은 닫힘 코드 | 프로세스가 끝나기까지 | leaving() 시작 / 끝 |",
        "|------|------|------|---:|---:|",
    ]
    for server, r in result["servers"].items():
        codes = ", ".join("없음 (TCP만 끊김)" if c == "None" else c for c in r["close_codes"])
        flags = f"`{' '.join(r['flags'])}`" if r["flags"] else "—"
        rows.append(
            f"| {SERIES_LABELS[server]} | {flags} | {codes} | {r['exit_seconds']:.2f}초 | "
            f"{r['leaving_started']} / {r['leaving_finished']} (연결 {r['connections']}개) |"
        )
    return "\n".join(rows)


def _footer(machine: Machine) -> list[str]:
    env = load(machine.files[0])["environment"]
    pkg = env["packages"]
    return [
        f"{env['cpu']} · {env['os']} · Python {env['python']}",
        f"Django {pkg['django']} · daphne {pkg['daphne']} · uvicorn {pkg['uvicorn']} · Granian {pkg['granian']}",
        f"같은 기계의 WebSocket 클라이언트 · 회차의 중앙값 · 원본 bench/results/*-servers-{machine.label}*.json",
    ]


# -- the charts ----------------------------------------------------------------------------


def chart(machine: Machine) -> str:
    groups: dict[str, list[Group]] = {m: [] for m in METRICS}
    for name in machine.files:
        result = load(name)
        by = cells(result)
        for items in result["settings"]["items"]:
            label = f"{short(result)} · 항목 {items}"
            for metric in METRICS:
                values = {
                    s: by[(items, s)].median[metric] for s in SERIES if (items, s) in by and by[(items, s)].rounds
                }
                if values:
                    groups[metric].append(Group(label, values))
    titles = {
        "per_connection_kb": ("연결당 서버 메모리 (KB), 낮을수록 좋다", _kb),
        "joins_per_s": ("초당 join, 높을수록 좋다", _rate),
        "events_per_s": ("초당 이벤트 (모든 연결이 한 번씩), 높을수록 좋다", _rate),
        "broadcast_ms": ("브로드캐스트 하나가 모든 연결에 닿기까지 (ms), 낮을수록 좋다", ms),
    }
    return render(
        f"servers-{machine.label}",
        f"ASGI 서버 비교 — {machine.title}",
        "같은 wireview 앱(testproj)을 서버만 바꿔 띄우고 실제 WebSocket 연결로 잰다",
        [Panel(titles[m][0], groups[m], titles[m][1]) for m in METRICS],
        _footer(machine),
        series_order=SERIES,
        series_labels=SERIES_LABELS,
        style=STYLE,
    )


def charts() -> dict[str, str]:
    return {f"bench-servers-{m.label}.svg": chart(m) for m in MACHINES}


# -- the numbers the prose quotes ----------------------------------------------------------


def facts() -> dict[str, str]:
    """The numbers docs/DEPLOYMENT.md's prose quotes, formatted as it quotes them."""
    out: dict[str, str] = {}
    for machine in MACHINES:
        main = next(load(n) for n in machine.files if load(n)["settings"]["connections"] == 2000)
        by = cells(main)
        for server in ("uvicorn-nodeflate", "granian"):
            for metric in METRICS:
                out[f"{machine.label}_{server}_{metric}"] = FORMAT[metric](by[(5, server)].median[metric])
        speedup = by[(5, "granian")].median["events_per_s"] / by[(5, "uvicorn-nodeflate")].median["events_per_s"]
        out[f"{machine.label}_events_ratio"] = f"{speedup:.2f}배"
        memory = (
            by[(5, "granian")].median["per_connection_kb"] / by[(5, "uvicorn-nodeflate")].median["per_connection_kb"]
        )
        out[f"{machine.label}_memory_ratio"] = f"{memory:.1f}배"
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", action="store_true", help="print the docs/DEPLOYMENT.md tables")
    parser.add_argument("--facts", action="store_true", help="print the numbers the prose quotes")
    args = parser.parse_args()
    if args.table:
        for machine in MACHINES:
            print(f"{machine.title}\n\n{table(machine)}\n")
        print(f"SIGTERM\n\n{shutdown_table()}")
        return
    if args.facts:
        print(json.dumps(facts(), indent=2, ensure_ascii=False))
        return
    IMAGES.mkdir(parents=True, exist_ok=True)
    for name, svg in charts().items():
        (IMAGES / name).write_text(svg, encoding="utf-8")
        print(f"wrote docs/images/{name}")


if __name__ == "__main__":
    main()
