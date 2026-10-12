"""Records each scene and writes the example's media (#213).

One process, one database file under ``build/example-media/``, emptied and seeded
before each scene, and one E2E server per scene (tests/testproj/e2e_server.py), so
nothing a scene leaves -- presence, a socket, a row -- reaches the next. The browser is
Playwright's Chromium, at a fixed viewport, locale and time zone.

Playwright's sync API runs an event loop on this thread, and Django refuses the ORM on
a thread with a running loop. So every database call goes to one worker thread of its
own (``_db``), as the server's own ORM calls go to the server's.

Each window is recorded from the moment its page opens; the clip is cut from where the
scene's ``play`` begins to where it ends, so the signing in and the first navigation of
``prepare`` are not in it. Two windows are cut at the same moment and put side by side.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time
import typing as t
from dataclasses import dataclass
from pathlib import Path

from . import budget
from .steps import Scene

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
IMG2WEBP = os.environ.get("IMG2WEBP") or shutil.which("img2webp") or "/opt/homebrew/bin/img2webp"
PNGQUANT = os.environ.get("PNGQUANT") or shutil.which("pngquant") or "/opt/homebrew/bin/pngquant"

#: The clip's frame rate. Ten is enough for typing and a list growing, and the WebP's size
#: is about proportional to it.
FPS = 10
#: What the WebP encoder is told: lossy at this quality, which keeps text sharp at 1x.
WEBP_QUALITY = 70
#: Held after the last step, so the final screen can be read before the clip loops.
HOLD_MS = 1500
#: Between the two windows of a shared scene.
DIVIDER = "0x9ca3af"


@dataclass
class Result:
    example: str
    seconds: float
    png: int
    webp: int
    mp4: int


def setup_django() -> None:
    """The test project's settings, its database moved to a file of our own."""
    for path in (budget.ROOT, budget.ROOT / "tests"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testproj.settings")
    # One server in this process: the in-memory layer reaches every socket it holds.
    os.environ.setdefault("WIREVIEW_TEST_LAYER", "memory")
    os.environ["WIREVIEW_RENDER_QUERIES_DIR"] = "off"
    import django
    from django.conf import settings

    budget.BUILD.mkdir(parents=True, exist_ok=True)
    database = budget.BUILD / "db.sqlite3"
    for leftover in budget.BUILD.glob("db.sqlite3*"):
        leftover.unlink()
    settings.DATABASES["default"]["NAME"] = str(database)
    # The test project logs every event at DEBUG; a recording wants to hear about failures only. In the
    # settings rather than on the logger: testproj/asgi.py runs django.setup() again, which reapplies them
    settings.LOGGING["loggers"]["wireview"]["level"] = "WARNING"
    # Each render's SQL, attributed and printed (#182), is for a developer at a terminal, not a recording
    settings.WIREVIEW = {**settings.WIREVIEW, "DEBUG_RENDER_QUERIES": False}
    django.setup()


class _Database:
    """Every ORM call, on one thread with no event loop (see the module docstring)."""

    def __init__(self) -> None:
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-db")

    def __call__(self, fn: t.Callable[[], t.Any]) -> t.Any:
        return self.pool.submit(self._closing(fn)).result()

    @staticmethod
    def _closing(fn: t.Callable[[], t.Any]) -> t.Callable[[], t.Any]:
        def run() -> t.Any:
            from django.db import connections

            try:
                return fn()
            finally:
                connections.close_all()

        return run

    def migrate(self) -> None:
        from django.core.management import call_command

        self(lambda: call_command("migrate", run_syncdb=True, verbosity=0))

    def reset(self, scene: Scene) -> dict[str, str]:
        from django.core.management import call_command

        def run() -> dict[str, str]:
            call_command("flush", interactive=False, verbosity=0)
            return scene.seed() if scene.seed else {}

        return self(run)

    def close(self) -> None:
        self.pool.shutdown()


def record_all(scenes: t.Sequence[Scene], log: t.Callable[[str], None] = print) -> list[Result]:
    setup_django()
    # testproj is on the path setup_django() gave it (tests/), which pyright does not read
    from playwright.sync_api import sync_playwright
    from testproj.e2e_server import serve  # pyright: ignore[reportMissingImports]

    database = _Database()
    results = []
    try:
        database.migrate()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for scene in scenes:
                    started = time.monotonic()
                    values = database.reset(scene)
                    with tempfile.TemporaryDirectory(prefix=f"media-{scene.example}-") as scratch:
                        with serve() as base_url:
                            clips, shots, span = _play(browser, scene, base_url, values, Path(scratch))
                        result = _encode(scene, clips, shots, span, Path(scratch))
                    result.seconds = time.monotonic() - started
                    log(
                        f"{scene.example:<14} {result.seconds:5.1f}s  png {result.png:>9,}  "
                        f"webp {result.webp:>10,}  mp4 {result.mp4:>10,}"
                    )
                    problems = budget.over_budget(scene.example)
                    if problems:
                        raise SystemExit("over budget:\n  " + "\n  ".join(problems))
                    results.append(result)
            finally:
                browser.close()
    finally:
        database.close()
    everything = sorted(p.parent.name for p in budget.EXAMPLES.glob("*/__init__.py"))
    if (size := budget.total(everything)) > budget.TOTAL_LIMIT:
        raise SystemExit(f"all the example media is {size:,} bytes, over its limit of {budget.TOTAL_LIMIT:,}")
    return results


def _play(browser: t.Any, scene: Scene, base_url: str, values: dict[str, str], scratch: Path):
    """Run the scene; return each window's raw recording and screenshot, and where the clip is in them."""
    from .player import POINTER_SCRIPT, label_script, perform

    width, height = scene.viewport
    contexts, pages, opened = {}, {}, {}
    for actor in scene.actors:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            color_scheme="light",
            record_video_dir=str(scratch / actor.name),
            record_video_size={"width": width, "height": height},
        )
        context.add_init_script(POINTER_SCRIPT)
        if scene.shared:
            context.add_init_script(label_script(actor.label or actor.name, actor.color))
        contexts[actor.name] = context
        pages[actor.name] = context.new_page()
        opened[actor.name] = time.monotonic()
    try:
        for step in scene.prepare:
            perform(pages, scene, step, base_url, values, paced=False)
        begin = time.monotonic()
        for step in scene.play:
            perform(pages, scene, step, base_url, values)
        for page in pages.values():
            # Wherever the last step left the pointer, it is not part of the final screen
            page.evaluate("document.getElementById('__media_pointer')?.style.setProperty('display', 'none')")
            # and off the last element it was over, which would still show its :hover style
            page.mouse.move(1, 1)
        pages[scene.actors[0].name].wait_for_timeout(HOLD_MS)
        end = time.monotonic()
        shots = {}
        for name, page in pages.items():
            shots[name] = scratch / f"{name}.png"
            page.screenshot(path=str(shots[name]))
    finally:
        for context in contexts.values():
            context.close()
    clips = {name: (Path(page.video.path()), begin - opened[name]) for name, page in pages.items()}
    return clips, shots, end - begin


def _ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _encode(scene: Scene, clips, shots, span: float, scratch: Path) -> Result:
    """The PNG and WebP into the example's media directory, the MP4 into build/."""
    budget.media_dir(scene.example).mkdir(parents=True, exist_ok=True)
    names = [actor.name for actor in scene.actors]
    inputs: list[str] = []
    for name in names:
        source, offset = clips[name]
        inputs += ["-ss", f"{offset:.3f}", "-t", f"{span:.3f}", "-i", str(source)]
    side_by_side = _side_by_side(len(names))

    # The final screen. Two windows: the same divider as the clip.
    screenshot = scratch / "screen.png"
    if len(names) == 1:
        shutil.copyfile(shots[names[0]], screenshot)
    else:
        images = [arg for name in names for arg in ("-i", str(shots[name]))]
        _ffmpeg(*images, "-filter_complex", side_by_side + "[v]", "-map", "[v]", str(screenshot))
    _quantise(screenshot)
    Path(screenshot).replace(budget.screenshot(scene.example))

    # The clip as frames, then as an animated WebP and an MP4.
    frames = scratch / "frames"
    frames.mkdir()
    chain = (side_by_side + "," if len(names) > 1 else "[0:v]") + f"fps={FPS}"
    _ffmpeg(*inputs, "-filter_complex", chain + "[v]", "-map", "[v]", str(frames / "%04d.png"))
    # The last frame is the screenshot's moment, held so it can be read before the loop starts again
    ordered = sorted(frames.glob("*.png"))
    webp = scratch / "clip.webp"
    delay = str(1000 // FPS)
    args = [IMG2WEBP, "-loop", "0", "-lossy", "-q", str(WEBP_QUALITY), "-m", "6", "-mixed"]
    for frame in ordered[:-1]:
        args += ["-d", delay, str(frame)]
    args += ["-d", "2500", str(ordered[-1])]
    subprocess.run([*args, "-o", str(webp)], check=True, capture_output=True)
    Path(webp).replace(budget.animation(scene.example))

    mp4 = budget.video(scene.example)
    _ffmpeg(
        "-framerate", str(FPS), "-i", str(frames / "%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-movflags", "+faststart",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", str(mp4),
    )  # fmt: skip
    return Result(
        scene.example,
        0.0,
        budget.screenshot(scene.example).stat().st_size,
        budget.animation(scene.example).stat().st_size,
        mp4.stat().st_size,
    )


def _side_by_side(count: int) -> str:
    """A filter graph putting ``count`` inputs left to right with a thin divider between them."""
    if count == 1:
        return "[0:v]null"
    padded = "".join(f"[{i}:v]pad=iw+4:ih:0:0:color={DIVIDER}[p{i}];" for i in range(count - 1))
    stacked = "".join(f"[p{i}]" for i in range(count - 1)) + f"[{count - 1}:v]"
    return f"{padded}{stacked}hstack=inputs={count}"


def _quantise(path: Path) -> None:
    """A 256-colour palette. A screenshot is mostly flat colour, and a gradient behind it (search,
    quiz) took a lossless PNG to 200 KB; the palette keeps text sharp at a fifth of that."""
    subprocess.run(
        [PNGQUANT, "--force", "--strip", "--speed", "1", "--output", str(path), "256", str(path)], check=True
    )
