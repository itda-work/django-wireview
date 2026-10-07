"""SVG charts and the numbers the documents quote, from one FastAPI comparison result.

    uv run python -m bench.compare_fastapi.chart            (make bench-fastapi-charts)
    uv run python -m bench.compare_fastapi.chart --table    the docs/PERFORMANCE.md table

``RESULT`` names the measurement the documents show. After a new ``make bench-fastapi``,
point it at the new file and run this again: the charts, the table and the README's
numbers all come from it, and tests/test_bench_fastapi.py fails until the
documents say what the file says.

Standard library only, and the same bytes for the same result: no dates, no randomness.
The colors are the first three slots of the validated categorical palette, stepped for
light and dark surfaces; the SVG follows the viewer's color scheme.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "bench" / "results" / "33bf6b9-fastapi.json"
IMAGES = ROOT / "docs" / "images"
#: The feed's stream fan-out (#178): the one broadcast wireview can send without rendering per
#: connection (``Broadcast``). The board's broadcast changes a field, which only a render can do,
#: so the comparison takes this path's numbers from that measurement, on the board's layer
#: (InMemory). The same file as ``bench.compare_fastapi.stream_fanout.RESULT``.
FEED_RESULT = ROOT / "bench" / "results" / "3ab5818-stream-fanout.json"

SERIES = ("wireview", "fastapi-react", "fastapi-vanilla")
SERIES_LABELS = {"wireview": "wireview", "fastapi-react": "FastAPI + React", "fastapi-vanilla": "FastAPI + 손 JS"}
SCENARIO_LABELS = {
    "change_value": "값 하나 변경",
    "insert_front": "50항목 맨 앞 삽입",
    "broadcast": "다른 사용자에게 브로드캐스트",
}

STYLE = """
.bg{fill:#fcfcfb}.t1{fill:#0b0b0b}.t2{fill:#52514e}.t3{fill:#898781}
.grid{stroke:#e1e0d9}.base{stroke:#c3c2b7}
.s0{fill:#2a78d6}.s1{fill:#eb6834}.s2{fill:#1baf7a}
@media (prefers-color-scheme:dark){
.bg{fill:#1a1a19}.t1{fill:#ffffff}.t2{fill:#c3c2b7}.t3{fill:#898781}
.grid{stroke:#2c2c2a}.base{stroke:#383835}
.s0{fill:#3987e5}.s1{fill:#d95926}.s2{fill:#199e70}
}
text{font-family:system-ui,-apple-system,"Segoe UI","Apple SD Gothic Neo","Noto Sans KR",sans-serif}
"""

WIDTH = 760
PAD = 24
LABEL_W = 196  # the group labels' column
VALUE_W = 196  # room right of the longest bar for its label
BAR_H = 14
BAR_GAP = 2
GROUP_GAP = 16


# -- formatting --------------------------------------------------------------------------


def ms(value: float) -> str:
    if value < 1:
        return f"{value:.2f} ms"
    if value < 100:
        return f"{value:.1f} ms"
    return f"{value:,.0f} ms"


def kb(value: float) -> str:
    return f"{value / 1000:.1f} KB"


def kb_value(value: float) -> str:
    """A value already in KB (a chart's KB axis is in KB too)."""
    return f"{value:.1f} KB"


def bytes_(value: float) -> str:
    return f"{value:,.0f} B"


def lines(value: float) -> str:
    return f"{value:.0f}줄"


# -- the drawing -------------------------------------------------------------------------


@dataclass
class Group:
    label: str
    values: dict[str, float]  # series -> value
    notes: dict[str, str] | None = None  # series -> extra text after the value


@dataclass
class Panel:
    title: str
    groups: list[Group]
    fmt: object  # value -> label text


def _nice_max(value: float) -> tuple[float, float]:
    """An axis end at or above ``value`` and its tick step (1, 2 or 5 times a power of ten)."""
    if value <= 0:
        return 1.0, 0.25
    raw = value / 4
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw)
    return step * math.ceil(value / step - 1e-9), step


def _num(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _bar(x: float, y: float, w: float, cls: str, title: str) -> str:
    """A bar anchored at the baseline on the left, its data end rounded (4px)."""
    r = min(4.0, w / 2, BAR_H / 2)
    if w <= 0:
        return ""
    d = (
        f"M{_num(x)} {_num(y)}h{_num(w - r)}a{_num(r)} {_num(r)} 0 0 1 {_num(r)} {_num(r)}"
        f"v{_num(BAR_H - 2 * r)}a{_num(r)} {_num(r)} 0 0 1 {_num(-r)} {_num(r)}h{_num(-(w - r))}z"
    )
    return f'<path class="{cls}" d="{d}"><title>{escape(title)}</title></path>'


def _tick(value: float, step: float) -> str:
    return f"{value:,.0f}" if step >= 1 else f"{value:g}"


def render(name: str, title: str, subtitle: str, panels: list[Panel], footer: list[str]) -> str:
    out: list[str] = []
    y = PAD + 18
    out.append(f'<text class="t1" x="{PAD}" y="{y}" font-size="17" font-weight="600">{escape(title)}</text>')
    y += 22
    out.append(f'<text class="t2" x="{PAD}" y="{y}" font-size="13">{escape(subtitle)}</text>')
    y += 26
    # Legend: every chart has three series
    x = PAD
    for i, series in enumerate(SERIES):
        out.append(f'<rect class="s{i}" x="{x}" y="{y - 10}" width="12" height="12" rx="3"/>')
        label = SERIES_LABELS[series]
        out.append(f'<text class="t2" x="{x + 18}" y="{y}" font-size="13">{escape(label)}</text>')
        x += 18 + 8 * len(label) + 28
    y += 18
    left = PAD + LABEL_W
    span = WIDTH - left - VALUE_W
    desc: list[str] = []
    for panel in panels:
        y += 22
        out.append(f'<text class="t1" x="{PAD}" y="{y}" font-size="14" font-weight="600">{escape(panel.title)}</text>')
        y += 12
        top = y
        end, step = _nice_max(max(v for g in panel.groups for v in g.values.values()))
        body: list[str] = []
        for group in panel.groups:
            series = [s for s in SERIES if s in group.values]
            height = len(series) * BAR_H + (len(series) - 1) * BAR_GAP
            label_y = y + height / 2 + 4.5
            body.append(f'<text class="t1" x="{PAD}" y="{_num(label_y)}" font-size="13">{escape(group.label)}</text>')
            for s in series:
                value = group.values[s]
                w = span * value / end
                text = panel.fmt(value)  # type: ignore[operator]
                note = (group.notes or {}).get(s)
                full = f"{SERIES_LABELS[s]} · {group.label}: {text}" + (f" ({note})" if note else "")
                desc.append(f"{panel.title} — {full}")
                body.append(_bar(left, y, w, f"s{SERIES.index(s)}", full))
                label = text + (f" ({note})" if note else "")
                body.append(
                    f'<text class="t2" x="{_num(left + w + 6)}" y="{_num(y + BAR_H - 3)}" font-size="12">'
                    f"{escape(label)}</text>"
                )
                y += BAR_H + BAR_GAP
            y += GROUP_GAP - BAR_GAP
        bottom = y - GROUP_GAP + 6
        # Grid and ticks under the bars, the baseline on the left
        ticks = []
        value = 0.0
        while value <= end + 1e-9:
            gx = left + span * value / end
            if value > 0:
                ticks.append(f'<line class="grid" x1="{_num(gx)}" y1="{top - 4}" x2="{_num(gx)}" y2="{_num(bottom)}"/>')
            ticks.append(
                f'<text class="t3" x="{_num(gx)}" y="{_num(bottom + 14)}" font-size="11" text-anchor="middle">'
                f"{_tick(value, step)}</text>"
            )
            value += step
        ticks.append(f'<line class="base" x1="{left}" y1="{top - 4}" x2="{left}" y2="{_num(bottom)}"/>')
        out += ticks + body
        y = bottom + 22
    y += 8
    for line in footer:
        out.append(f'<text class="t3" x="{PAD}" y="{y}" font-size="11">{escape(line)}</text>')
        y += 16
    height = y + PAD - 12
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}"'
        f' role="img" aria-labelledby="{name}-title {name}-desc">'
        f'<title id="{name}-title">{escape(title)}</title>'
        f'<desc id="{name}-desc">{escape(subtitle + ". " + "; ".join(desc))}</desc>'
        f"<style>{STYLE.strip()}</style>"
        f'<rect class="bg" width="{WIDTH}" height="{height}" rx="8"/>'
    )
    return head + "\n" + "\n".join(out) + "\n</svg>\n"


# -- the charts --------------------------------------------------------------------------


def _summary(result: dict) -> dict[str, dict]:
    return {name: result["implementations"][name]["summary"] for name in SERIES}


def _footer(result: dict) -> list[str]:
    env = result["environment"]
    pkg = env["packages"]
    react = env["client_packages"]["react"]
    method = result["method"]
    return [
        f"{env['cpu']} · macOS {env['os_version']} · Python {env['python']} · Django {pkg['django']} · "
        f"FastAPI {pkg['fastapi']} · React {react} · {env['browser'].split(' (')[0]} headless",
        f"uvicorn 1프로세스, localhost · {method['rounds']}회차의 중앙값 · 원본 bench/results/{RESULT.name}",
    ]


def _feed(feed: dict | None) -> dict[str, dict]:
    """The feed's fan-out on InMemory: wireview's notification and Broadcast paths, and FastAPI."""
    summary = (feed if feed is not None else load(FEED_RESULT))["summary"]
    return {
        "notified": summary["wireview"]["memory"],
        "broadcast": summary["wireview-broadcast"]["memory"],
        "fastapi": summary["fastapi"]["none"],
    }


def _feed_rounds(x: dict) -> str:
    """A feed measurement's rounds, lowest to highest, in the precision of ``ms``."""
    rounds = x["fanout_ms_rounds"]
    return f"{ms(min(rounds)).removesuffix(' ms')}~{ms(max(rounds))}"


def charts(result: dict, feed: dict | None = None) -> dict[str, str]:
    s = _summary(result)
    f = _feed(feed)
    feed_rounds = len(f["broadcast"]["fanout_ms_rounds"])
    footer = _footer(result)
    method = result["method"]

    def scenario_groups(metric: str, with_p95: bool = True) -> list[Group]:
        return [
            Group(
                SCENARIO_LABELS[name],
                {n: s[n][name][metric]["median"] for n in SERIES},
                {n: f"p95 {ms(s[n][name][metric]['p95'])}" for n in SERIES} if with_p95 else None,
            )
            for name in SCENARIO_LABELS
        ]

    out = {}
    out["latency"] = render(
        "latency",
        "클릭에서 화면까지",
        f"브라우저 안에서 잰 시간, 낮을수록 좋다. 시나리오마다 {method['clicks_per_scenario']}번 클릭",
        [
            Panel("화면에 그려지기까지, 다음 페인트 (ms)", scenario_groups("paint_ms"), ms),
            Panel("DOM이 바뀌기까지 (ms) — wireview는 패치를 다음 프레임에 쓴다", scenario_groups("dom_ms"), ms),
        ],
        footer,
    )

    def traffic(name: str) -> Group:
        return Group(
            SCENARIO_LABELS[name],
            {n: s[n][name]["clicker_sent_bytes"] + s[n][name]["clicker_received_bytes"] for n in SERIES},
            {n: _sent_received(s[n][name], "clicker") for n in SERIES},
        )

    out["bytes"] = render(
        "bytes",
        "상호작용 하나의 WebSocket 전송량",
        "페이로드 바이트, 낮을수록 좋다. 프레임 헤더 제외, permessage-deflate 전",
        [
            Panel(
                "누른 사용자가 보내고 받은 것 (B)",
                [traffic(name) for name in SCENARIO_LABELS],
                bytes_,
            ),
            Panel(
                "브로드캐스트를 받은 다른 사용자 (B)",
                [Group("받은 것", {n: s[n]["broadcast"]["watcher_received_bytes"] for n in SERIES})],
                bytes_,
            ),
        ],
        footer,
    )

    def download(n: str, key: str) -> float:
        d = s[n]["download"]
        return (d[f"html_{key}"] + d[f"js_{key}"]) / 1000

    out["first-load"] = render(
        "first-load",
        "첫 화면",
        "캐시 없는 새 브라우저로 다른 사이트에서 들어올 때, 낮을수록 좋다",
        [
            Panel(
                "내려받는 HTML + JS (KB, 1 KB = 1,000 B)",
                [
                    Group("gzip -6", {n: download(n, "gzip_bytes") for n in SERIES}),
                    Group("원본", {n: download(n, "bytes") for n in SERIES}),
                ],
                kb_value,
            ),
            Panel(
                "걸린 시간 (ms)",
                [
                    Group("첫 화면 (FCP)", {n: s[n]["first_contentful_paint_ms"]["median"] for n in SERIES}),
                    Group("조작할 수 있게 되기까지", {n: s[n]["interactive_ms"]["median"] for n in SERIES}),
                ],
                ms,
            ),
        ],
        footer,
    )

    out["server"] = render(
        "server",
        "서버가 하는 일",
        "같은 ASGI 래퍼로 잰 서버 처리 시간과 브로드캐스트 팬아웃, 낮을수록 좋다",
        [
            Panel(
                "메시지 하나의 서버 처리, 받은 때부터 보낼 때까지 (ms)",
                [
                    Group(SCENARIO_LABELS[name], {n: s[n][name]["server_ms"]["median"] for n in SERIES})
                    for name in ("change_value", "insert_front")
                ],
                ms,
            ),
            Panel(
                f"브로드캐스트 하나가 연결 {method['fanout_connections']:,}개에 모두 닿기까지 (ms)",
                [
                    Group("보드 공지 (알림)", {n: s[n]["fanout_ms"]["median"] for n in SERIES}),
                    Group(
                        "피드 새 항목 (Broadcast)",
                        {
                            "wireview": f["broadcast"]["fanout_ms"],
                            **{n: f["fastapi"]["fanout_ms"] for n in SERIES[1:]},
                        },
                    ),
                ],
                ms,
            ),
        ],
        [
            *footer,
            f"피드의 새 항목: 같은 서버·레이어, {feed_rounds}회차의 중앙값 · 원본 bench/results/{FEED_RESULT.name}",
        ],
    )

    loc = result["loc"]
    out["loc"] = render(
        "loc",
        "같은 화면을 만든 코드 줄 수",
        "사람이 쓴 줄, 빈 줄·주석·docstring 제외 (bench/compare_fastapi/loc.py)",
        [
            Panel(
                "줄 수",
                [
                    Group("앱 코드 (서버 + 화면)", {n: loc[n]["app"] for n in SERIES}),
                    Group("프로젝트 골격 (설정·빌드)", {n: loc[n]["scaffolding"] for n in SERIES}),
                ],
                lines,
            )
        ],
        [footer[0].split(" · ")[0] + f" · 원본 bench/results/{RESULT.name}"],
    )
    return out


# -- the numbers the documents quote ------------------------------------------------------


def facts(result: dict, feed: dict | None = None) -> dict[str, str]:
    """Every number README's "숫자" section quotes, formatted as it quotes them."""
    s = _summary(result)
    f = _feed(feed)
    w, r, v = (s[n] for n in SERIES)

    def gz(x: dict) -> float:
        return x["download"]["html_gzip_bytes"] + x["download"]["js_gzip_bytes"]

    def change_bytes(x: dict) -> float:
        return x["change_value"]["clicker_sent_bytes"] + x["change_value"]["clicker_received_bytes"]

    return {
        "paint_wireview": ms(w["change_value"]["paint_ms"]["median"]),
        "paint_react": ms(r["change_value"]["paint_ms"]["median"]),
        "paint_vanilla": ms(v["change_value"]["paint_ms"]["median"]),
        "server_wireview": ms(w["change_value"]["server_ms"]["median"]),
        "server_react": ms(r["change_value"]["server_ms"]["median"]),
        "bytes_wireview": bytes_(change_bytes(w)),
        "bytes_react": bytes_(change_bytes(r)),
        "gzip_wireview": kb(gz(w)),
        "gzip_react": kb(gz(r)),
        "gzip_vanilla": kb(gz(v)),
        "fcp_wireview": ms(w["first_contentful_paint_ms"]["median"]),
        "fcp_react": ms(r["first_contentful_paint_ms"]["median"]),
        "fcp_vanilla": ms(v["first_contentful_paint_ms"]["median"]),
        "fanout_connections": f"{result['method']['fanout_connections']:,}",
        "fanout_wireview": ms(w["fanout_ms"]["median"]),
        "fanout_react": ms(r["fanout_ms"]["median"]),
        "fanout_feed_wireview": ms(f["broadcast"]["fanout_ms"]),
        "fanout_feed_fastapi": ms(f["fastapi"]["fanout_ms"]),
    }


def table(result: dict, feed: dict | None = None) -> str:
    """The docs/PERFORMANCE.md table: every metric, median over rounds, with p95 or the rounds' range."""
    s = _summary(result)
    f = _feed(feed)

    def spread(x: dict) -> str:
        return f"{ms(x['median'])} (p95 {ms(x['p95'])}, 회차 {_range(x)})"

    rows: list[tuple[str, list[str]]] = []
    for name, label in SCENARIO_LABELS.items():
        rows.append((f"{label}: 클릭→페인트", [spread(s[n][name]["paint_ms"]) for n in SERIES]))
        rows.append((f"{label}: 클릭→DOM", [spread(s[n][name]["dom_ms"]) for n in SERIES]))
        if name != "broadcast":
            rows.append((f"{label}: 서버 처리", [spread(s[n][name]["server_ms"]) for n in SERIES]))
        rows.append(
            (
                f"{label}: 보냄 / 받음",
                [
                    f"{bytes_(s[n][name]['clicker_sent_bytes'])} / {bytes_(s[n][name]['clicker_received_bytes'])}"
                    for n in SERIES
                ],
            )
        )
    rows.append(
        ("브로드캐스트: 다른 사용자가 받음", [bytes_(s[n]["broadcast"]["watcher_received_bytes"]) for n in SERIES])
    )
    rows.append(
        (
            f"보드의 공지 팬아웃 (연결 {result['method']['fanout_connections']:,}개)",
            [f"{ms(s[n]['fanout_ms']['median'])} (회차 {_range(s[n]['fanout_ms'])})" for n in SERIES],
        )
    )
    fastapi = f"{ms(f['fastapi']['fanout_ms'])} (회차 {_feed_rounds(f['fastapi'])})"
    feed_rounds = len(f["broadcast"]["fanout_ms_rounds"])
    rows.append(
        (
            f"피드의 새 항목 팬아웃 (연결 {result['method']['fanout_connections']:,}개, {feed_rounds}회차)",
            [
                f"Broadcast {ms(f['broadcast']['fanout_ms'])} (회차 {_feed_rounds(f['broadcast'])}), "
                f"알림 {ms(f['notified']['fanout_ms'])} (회차 {_feed_rounds(f['notified'])})",
                fastapi,
                fastapi,
            ],
        )
    )
    rows.append(("첫 화면: HTML (gzip / 원본)", [_pair(s[n]["download"], "html") for n in SERIES]))
    rows.append(("첫 화면: JS (gzip / 원본)", [_pair(s[n]["download"], "js") for n in SERIES]))
    rows.append(
        (
            "첫 화면: WebSocket 보냄 / 받음",
            [
                f"{bytes_(s[n]['download']['ws_sent_bytes'])} / {bytes_(s[n]['download']['ws_received_bytes'])}"
                for n in SERIES
            ],
        )
    )
    rows.append(("첫 화면: FCP", [spread(s[n]["first_contentful_paint_ms"]) for n in SERIES]))
    rows.append(("첫 화면: 목록이 DOM에", [spread(s[n]["first_screen_ms"]) for n in SERIES]))
    rows.append(("첫 화면: 조작 가능", [spread(s[n]["interactive_ms"]) for n in SERIES]))
    loc = result["loc"]
    rows.append(("코드 줄 수: 앱 / 골격", [f"{loc[n]['app']} / {loc[n]['scaffolding']}" for n in SERIES]))
    header = "| 지표 | " + " | ".join(SERIES_LABELS[n] for n in SERIES) + " |"
    rule = "|------|" + "---:|" * len(SERIES)
    return "\n".join([header, rule] + [f"| {label} | " + " | ".join(cells) + " |" for label, cells in rows])


def _range(x: dict) -> str:
    """The rounds' medians, lowest to highest, in the precision of ``ms``."""
    return f"{ms(x['median_min']).removesuffix(' ms')}~{ms(x['median_max'])}"


def _sent_received(x: dict, who: str) -> str:
    return f"보냄 {bytes_(x[f'{who}_sent_bytes'])} · 받음 {bytes_(x[f'{who}_received_bytes'])}"


def _pair(download: dict, key: str) -> str:
    return f"{kb(download[f'{key}_gzip_bytes'])} / {kb(download[f'{key}_bytes'])}"


def load(path: Path = RESULT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", action="store_true", help="print the docs/PERFORMANCE.md table")
    parser.add_argument("--facts", action="store_true", help="print the numbers README quotes")
    args = parser.parse_args()
    result = load()
    if args.table:
        print(table(result))
        return
    if args.facts:
        print(json.dumps(facts(result), indent=2, ensure_ascii=False))
        return
    IMAGES.mkdir(parents=True, exist_ok=True)
    for name, svg in charts(result).items():
        path = IMAGES / f"bench-fastapi-{name}.svg"
        path.write_text(svg, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
