"""Guard: every example shows what it does, from media a script made, within a size budget (#213).

The example READMEs had no picture: what an example showed was known only after
running it. ``make example-media`` records each example's scene
(scripts/example_media/scenes.py) into ``examples/<name>/media/``. Recording needs a
browser and ffmpeg, so it is not part of the suite; what is checked here is what it
left: a scene and both files for every example, each file within its budget
(scripts/example_media/budget.py, which the generator enforces too), and each
README and table pointing at them.
"""

import re
from pathlib import Path

import pytest

from scripts.example_media import budget
from scripts.example_media.scenes import by_name

pytestmark = pytest.mark.unit

ROOT = budget.ROOT
RAW = "https://raw.githubusercontent.com/itda-work/django-wireview/main/examples"


def _examples() -> list[str]:
    return sorted(path.name for path in budget.EXAMPLES.iterdir() if (path / "__init__.py").is_file())


EXAMPLES = _examples()


def test_every_example_has_a_scene_and_no_scene_is_for_anything_else():
    assert sorted(by_name()) == EXAMPLES


@pytest.mark.parametrize("name", EXAMPLES)
def test_the_media_is_there_and_within_budget(name):
    assert budget.over_budget(name) == []


def test_all_the_media_together_is_within_budget():
    total = budget.total(EXAMPLES)
    assert total <= budget.TOTAL_LIMIT, f"{total:,} bytes of example media, over {budget.TOTAL_LIMIT:,}"


def test_nothing_else_is_committed_beside_the_media():
    """The MP4 and the raw recordings go to build/: a stray file here would be committed with the rest."""
    for name in EXAMPLES:
        found = {path.name for path in budget.media_dir(name).iterdir()}
        assert found == {f"{name}.png", f"{name}.webp"}, name


@pytest.mark.parametrize("name", EXAMPLES)
def test_the_readme_shows_the_clip_under_its_title_and_links_the_screenshot(name):
    text = (budget.EXAMPLES / name / "README.md").read_text(encoding="utf-8")
    top = text.split("\n## ", 1)[0]

    assert f"](media/{name}.webp)" in top, "the clip, before the first section"
    assert f"(media/{name}.png)" in top, "the final screen, linked"
    assert by_name()[name].caption in top, "what the clip shows, as the scene says it"


def test_the_index_and_the_root_table_show_every_examples_screenshot():
    index = (budget.EXAMPLES / "README.md").read_text(encoding="utf-8")
    root = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in EXAMPLES:
        assert re.search(rf'^\| \[{name}\]\(\./{name}/\) \|.*<img src="\./{name}/media/{name}\.png"', index, re.M), name
        # Absolute, because the root README is also the PyPI page (hatch_build.py pins it to the tag)
        assert re.search(rf'^\| \[{name}\]\(.*<img src="{RAW}/{name}/media/{name}\.png"', root, re.M), name


def test_the_budget_is_what_the_document_says():
    """examples/README.md states the limits; the numbers are budget.py's."""
    text = (budget.EXAMPLES / "README.md").read_text(encoding="utf-8")
    for limit in (budget.PNG_LIMIT, budget.WEBP_LIMIT, budget.TOTAL_LIMIT):
        assert f"{limit // 1000:,}KB" in text or f"{limit / 1_000_000:g}MB" in text, limit


def test_the_scenes_and_the_budget_import_without_django():
    """The demo site (#214) and this test read them without setting Django up."""
    assert Path(budget.__file__).read_text(encoding="utf-8").count("import django") == 0
    source = (ROOT / "scripts" / "example_media" / "scenes.py").read_text(encoding="utf-8")
    assert not re.search(r"^from (django|examples)|^import (django|examples)", source, re.M)
