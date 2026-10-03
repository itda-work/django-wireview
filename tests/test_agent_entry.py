"""Guard: the way in for an AI agent says what the README, tutorial 01 and the build say (#164, #165).

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


def _section(text: str, heading: str) -> str:
    """The lines under ``heading`` up to the next heading of its level or higher, code blocks skipped over."""
    level = len(heading.split(" ", 1)[0])
    lines = text.split("\n")
    start = lines.index(heading) + 1
    fenced = False
    for number in range(start, len(lines)):
        if lines[number].startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(rf"#{{1,{level}}} ", lines[number]):
            return "\n".join(lines[start:number])
    return "\n".join(lines[start:])


def _existing_project_heading(text: str) -> str:
    from scripts.docs_site.build import EXISTING_PROJECT

    headings = [line for line in text.splitlines() if line.startswith("## ") and line.endswith(EXISTING_PROJECT)]
    assert len(headings) == 1, headings
    return headings[0]


def test_an_existing_project_sets_wireview_up_before_the_skill_command():
    """``wireview_agent_setup`` exists only once ``wireview`` is in INSTALLED_APPS: step 2 says so
    for a project that is not the starter's, and sends it to the tutorial's section that does it (#165)."""
    step2 = _prose().split("\n2. ", 1)[1].split("\n3. ", 1)[0]
    assert "이미 있는 프로젝트면" in step2 and "$existing_project" in step2
    for setting in ("INSTALLED_APPS", "daphne", "ASGI_APPLICATION", "CHANNEL_LAYERS", "asgi.py", "wireview.urls"):
        assert setting in step2, setting


def test_the_tutorials_existing_project_section_holds_all_the_wiring():
    """One section of tutorial 01 is everything a project needs, so llms.txt can send an agent to it alone."""
    tutorial = nav.tutorials()[0].path.read_text(encoding="utf-8")
    section = _section(tutorial, _existing_project_heading(tutorial))
    for needed in (
        "'daphne',",
        "'wireview',",
        "'channels',",
        "ASGI_APPLICATION = ",
        "CHANNEL_LAYERS = ",
        "### asgi.py 수정",
        "websocket_urlpatterns",
        "path('', include('wireview.urls'))",
    ):
        assert needed in section, needed


def test_llms_txt_names_the_existing_project_section_by_its_heading():
    """The build makes the anchor from the heading (nav.slug), the way the page's ids are made."""
    from scripts.docs_site.build import _existing_project

    first = nav.tutorials()[0]
    heading = _existing_project_heading(first.path.read_text(encoding="utf-8"))
    assert _existing_project(first) == f"{nav.ORIGIN}{first.url}#{nav.slug(heading[3:])}"
    assert "#2-이미-있는-프로젝트에-붙이기" in _existing_project(first)


def _code(text: str, marker: str) -> str:
    """The one fenced block of ``text`` that contains ``marker``."""
    blocks = re.findall(r"^```\w*\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    found = [block for block in blocks if marker in block]
    assert len(found) == 1, (marker, len(found))
    return found[0]


def test_the_readme_wires_a_project_as_tutorial_01_does():
    """The README's setup and the tutorial's section are two copies of the same wiring; they must not drift."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tutorial = nav.tutorials()[0].path.read_text(encoding="utf-8")
    section = _section(tutorial, _existing_project_heading(tutorial))
    marker = "ProtocolTypeRouter({"
    assert _code(readme, marker).replace("project_name", "myproject") == _code(section, marker)
    for setting in ("ASGI_APPLICATION", "InMemoryChannelLayer", "'daphne',", "'wireview',", "'channels',"):
        assert setting in _code(readme, "ASGI_APPLICATION") and setting in _code(section, "ASGI_APPLICATION"), setting
    assert 'path("", include("wireview.urls"))' in readme


def test_the_readme_opens_with_what_a_reader_decides_on_before_the_agent_prompt():
    """README's first screen (#167, #172): what it is and a link to the AI section, an example, when to
    use it and then when not to, numbers, install, and then the agent prompt."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    first = readme.split("\n```", 1)[0]
    assert "](#ai-에이전트로-시작하기)" in first and "llms.txt" not in first
    headings = [line for line in readme.splitlines() if line.startswith("## ")][:6]
    assert headings == [
        "## 이럴 때 쓰세요",
        "## 이럴 땐 쓰지 마세요",
        "## 숫자",
        "## 설치",
        "## AI 에이전트로 시작하기",
        "## 무엇이 포함되어 있나요?",
    ]
    assert readme.index("```html") < readme.index("## 이럴 때 쓰세요") < readme.index("## 이럴 땐 쓰지 마세요")


def test_the_readme_and_the_skill_page_name_where_the_build_writes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.split("## AI 에이전트로 시작하기", 1)[1].split("\n## ", 1)[0]
    assert f"]({LLMS_URL})" in start
    assert _blocks(start)[0].startswith(f"{LLMS_URL} ")
    assert readme.index("## AI 에이전트로 시작하기") < readme.index("## 무엇이 포함되어 있나요?")
    # The prompt asks the agent to consult llms.txt, not to obey it (#167).
    assert "참고해" in _blocks(start)[0] and "안내대로" not in _blocks(start)[0]
    skill_page = (ROOT / "docs" / "features" / "agent-skill.md").read_text(encoding="utf-8")
    skill_url = nav.ORIGIN + SITE.skill()[0].url
    assert f"`{LLMS_URL}`" in skill_page and f"`{skill_url}`" in skill_page
