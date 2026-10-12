"""Where each example's media lives and how large it may be (#213).

Read by the generator, which fails a scene whose output is over budget, and by
tests/test_example_media.py, which fails a commit that is. Standard library only:
the test imports it without Django, Playwright or ffmpeg.

The formats follow from where a README is read:

- GitHub renders ``<img>`` and Markdown images of a repository path, and does not play
  a repository-relative ``.mp4``: a video has to be uploaded through the web editor,
  which no script can reproduce.
- PyPI shows the root README with every link absolute (hatch_build.py pins them to the
  release tag), and an image there is fetched by the reader's browser.
- The documentation site copies a README image into its own assets
  (scripts/docs_site/render.py), and has no page per example.

So what is committed is a PNG of the final screen and an animated WebP of the scene,
both of which all three show. The MP4 is written to ``build/example-media/`` and not
committed: nothing that reads the repository could play it, and the demo site (#214)
regenerates it from the same scenes.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLES = ROOT / "examples"

#: Where the uncommitted by-products go: the MP4s, the database, the raw recordings.
BUILD = ROOT / "build" / "example-media"

#: Per file, in bytes: about one and a half times the largest WebP and twice the largest
#: PNG the scenes made when the budget was set (#213). PNGs of 11-46 KB: one viewport at
#: device scale 1, mostly flat colour, quantised to 256 colours. WebPs of 134-711 KB: ten
#: to fifteen seconds at ten frames a second, lossy, two windows side by side for the
#: shared scenes. A file over it is a scene that grew -- a
#: longer clip, a bigger viewport, a photo -- and a reason to look before committing it.
PNG_LIMIT = 100_000
WEBP_LIMIT = 1_000_000

#: All examples' media together. The fourteen came to about 4.3 MB; fourteen at the
#: per-file limits would be 15.4 MB, which this keeps the repository from drifting toward.
TOTAL_LIMIT = 6_000_000


def media_dir(name: str) -> Path:
    return EXAMPLES / name / "media"


def screenshot(name: str) -> Path:
    """The final screen: what the README shows first and the tables show as a thumbnail."""
    return media_dir(name) / f"{name}.png"


def animation(name: str) -> Path:
    """The scene, played: what the example's README shows under the screenshot."""
    return media_dir(name) / f"{name}.webp"


def video(name: str) -> Path:
    """The same scene as an MP4, for the demo site. Not committed."""
    return BUILD / f"{name}.mp4"


def over_budget(name: str) -> list[str]:
    """What is wrong with an example's committed media, as sentences. Empty when it is within budget."""
    problems = []
    for path, limit in ((screenshot(name), PNG_LIMIT), (animation(name), WEBP_LIMIT)):
        if not path.is_file():
            problems.append(f"{path.relative_to(ROOT)} is missing")
        elif (size := path.stat().st_size) > limit:
            problems.append(f"{path.relative_to(ROOT)} is {size:,} bytes, over its limit of {limit:,}")
    return problems


def total(names: "list[str]") -> int:
    """The committed media of these examples, in bytes; a missing file counts nothing."""
    return sum(path.stat().st_size for name in names for path in (screenshot(name), animation(name)) if path.is_file())
