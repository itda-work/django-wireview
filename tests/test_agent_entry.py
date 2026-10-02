"""Guard: the way in for an AI agent says what the README, tutorial 01 and the build say (#164).

The README's "AI 에이전트로 시작하기" prompt sends an agent to llms.txt, and llms.txt's prose
(scripts/docs_site/templates/llms.txt) gives it commands to run. Each names something another
file owns -- the URL the build writes llms.txt at, the starter command the README and the first
tutorial give, the management commands wireview ships -- and drifts silently when that file
changes. These read the sources, not a build, so ``make docs-site`` runs them before it builds
(Makefile's DOCS_GUARDS): the release's ``make docs-site-bundle`` goes through them too.
"""

from __future__ import annotations

import re
from string import Template

import pytest

from scripts.docs_site import nav

pytestmark = pytest.mark.unit

ROOT = nav.ROOT
SITE = nav.Site.load()
LLMS_URL = nav.ORIGIN + SITE.llms_url
TEMPLATE = (ROOT / "scripts" / "docs_site" / "templates" / "llms.txt").read_text(encoding="utf-8")


def _prose() -> str:
    """The template as llms.txt carries it, the build's values left as their names."""
    return Template(TEMPLATE).safe_substitute()


def _blocks(text: str) -> list[str]:
    """The lines of every fenced code block, unindented."""
    lines, fenced = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
        elif fenced:
            lines.append(line.strip())
    return lines


def test_the_commands_in_llms_txt_are_the_readmes_and_the_first_tutorials():
    """What the agent runs is what a person following the README or tutorial 01 runs."""
    commands = _blocks(_prose())
    assert commands == [
        "pip install django-wireview daphne",
        next(line for line in _blocks((ROOT / "README.md").read_text(encoding="utf-8")) if "startproject" in line),
    ]
    tutorial = nav.tutorials()[0]
    assert tutorial.source == "docs/tutorials/01-getting-started.md"
    for source in ("README.md", tutorial.source):
        assert set(commands) <= set(_blocks((ROOT / source).read_text(encoding="utf-8"))), source


def test_the_management_commands_llms_txt_names_exist():
    from django.core.management import get_commands

    named = set(re.findall(r"`python manage\.py (\w+)", _prose()))
    assert named == {"wireview_agent_setup", "check"}
    assert named <= set(get_commands())


def test_llms_txt_names_the_getting_started_tutorial_by_the_build_not_by_hand():
    """The tutorial's address comes from docs/site.toml (the build's $getting_started), and it is
    tutorial 01, not the tutorial section's table of contents."""
    assert "$getting_started" in TEMPLATE
    assert "https://" not in TEMPLATE, "a hand-written URL"
    first = nav.tutorials()[0]
    assert first.slug and first.section == "tutorial"
    assert first.title == "시작하기"


def test_an_existing_project_sets_wireview_up_before_the_skill_command():
    """``wireview_agent_setup`` exists only once ``wireview`` is in INSTALLED_APPS: step 2 says so
    for a project that is not the starter's, and names the tutorial sections that do it."""
    step2 = _prose().split("\n2. ", 1)[1].split("\n3. ", 1)[0]
    assert "이미 있는 프로젝트면" in step2
    for setting in ("INSTALLED_APPS", "daphne", "ASGI_APPLICATION", "CHANNEL_LAYERS", "asgi.py", "wireview.urls"):
        assert setting in step2, setting
    tutorial = nav.tutorials()[0].path.read_text(encoding="utf-8")
    for heading in ("## 1. 설치", "## 2. Django 설정", "## 3. 첫 번째 컴포넌트 만들기", "### 뷰 및 URL 설정"):
        assert f"\n{heading}\n" in tutorial, heading
    assert "include('wireview.urls')" in tutorial.split("### 뷰 및 URL 설정", 1)[1]


def test_the_readme_and_the_skill_page_name_where_the_build_writes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.split("## AI 에이전트로 시작하기", 1)[1].split("\n## ", 1)[0]
    assert f"]({LLMS_URL})" in start
    assert _blocks(start)[0].startswith(f"{LLMS_URL} ")
    assert readme.index("## AI 에이전트로 시작하기") < readme.index("## 무엇이 포함되어 있나요?")
    skill_page = (ROOT / "docs" / "features" / "agent-skill.md").read_text(encoding="utf-8")
    skill_url = nav.ORIGIN + SITE.skill()[0].url
    assert f"`{LLMS_URL}`" in skill_page and f"`{skill_url}`" in skill_page
