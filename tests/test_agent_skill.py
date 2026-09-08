"""Guard: the skill shipped to app developers must stay installable and honest.

``skills/wireview/`` is the canonical copy. This repository dogfoods it through
a symlink, the wheel carries it, and ``wireview_agent_setup`` installs it into a
user's project. Each of those three links breaks silently, so they are tested.
"""

import tomllib
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from wireview.management.commands.wireview_agent_setup import skill_source

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "wireview"
DOGFOOD = ROOT / ".claude" / "skills" / "wireview"


@pytest.mark.unit
def test_the_canonical_skill_has_its_references():
    assert (SKILL / "SKILL.md").is_file()
    references = sorted(p.name for p in (SKILL / "references").glob("*.md"))
    assert references == ["component.md", "streams-uploads.md", "templates.md", "testing.md"]


@pytest.mark.unit
def test_every_reference_the_skill_links_to_exists():
    text = (SKILL / "SKILL.md").read_text()
    linked = {name for name in ("component", "templates", "streams-uploads", "testing") if name in text}
    missing = sorted(name for name in linked if not (SKILL / "references" / f"{name}.md").is_file())
    assert missing == [], f"SKILL.md links at references that do not exist: {missing}"
    assert len(linked) == 4, "SKILL.md no longer routes to every reference"


@pytest.mark.unit
def test_the_repository_dogfoods_the_canonical_skill():
    assert DOGFOOD.is_symlink(), ".claude/skills/wireview must be a symlink, not a copy"
    assert DOGFOOD.resolve() == SKILL.resolve()


@pytest.mark.unit
def test_the_wheel_carries_the_skill():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include["skills/wireview"] == "wireview/agent_skills/wireview"
    assert "/skills" in pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]


@pytest.mark.unit
def test_the_setup_command_finds_the_skill():
    assert (skill_source() / "SKILL.md").is_file()


@pytest.mark.unit
def test_the_setup_command_installs_into_a_project(tmp_path: Path):
    out = StringIO()
    call_command("wireview_agent_setup", target=str(tmp_path), stdout=out)

    installed = tmp_path / ".claude" / "skills" / "wireview"
    assert (installed / "SKILL.md").read_text() == (SKILL / "SKILL.md").read_text()
    assert (installed / "references" / "component.md").is_file()
    assert "SKILL.md" in out.getvalue()


@pytest.mark.unit
def test_the_setup_command_refuses_to_clobber(tmp_path: Path):
    from django.core.management.base import CommandError

    call_command("wireview_agent_setup", target=str(tmp_path), stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("wireview_agent_setup", target=str(tmp_path), stdout=StringIO())

    call_command("wireview_agent_setup", target=str(tmp_path), force=True, stdout=StringIO())
    assert (tmp_path / ".claude" / "skills" / "wireview" / "SKILL.md").is_file()


@pytest.mark.unit
def test_the_setup_command_leaves_a_dogfooding_symlink_alone(tmp_path: Path):
    destination = tmp_path / ".claude" / "skills"
    destination.mkdir(parents=True)
    (destination / "wireview").symlink_to(SKILL)

    out = StringIO()
    call_command("wireview_agent_setup", target=str(tmp_path), force=True, stdout=out)

    assert (destination / "wireview").is_symlink()
    assert "symlink" in out.getvalue()
