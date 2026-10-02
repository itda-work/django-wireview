"""Guard: docs/site.toml classifies every user document once, at an address that holds (#158).

The documentation site (itda.work/wireview/) is built from the release tag and shows only
that release, so a document nobody classified is a page that silently never ships, and a
URL that moves without a redirect is a 404 for every link already out there. The
navigation lives in one file; these tests hold the documents and the redirects to it.
"""

import re
from collections import Counter

import pytest

from scripts.docs_site import nav as site_nav
from scripts.docs_site.nav import SLUG, Page

pytestmark = pytest.mark.unit

PAGES = site_nav.pages()
SOURCES = [page.source for page in PAGES]
URLS = {page.url for page in PAGES}


def test_every_published_document_appears_once():
    assert [source for source, n in Counter(SOURCES).items() if n > 1] == []


def test_every_page_is_a_tracked_document():
    tracked = set(site_nav.tracked_documents())
    assert [source for source in SOURCES if source not in tracked] == []


def test_every_document_is_published_or_excluded():
    """A new document has to be put somewhere: a page of a section, or an [exclude] pattern."""
    published = set(SOURCES)
    unclassified = [
        path for path in site_nav.tracked_documents() if path not in published and not site_nav.excluded(path)
    ]
    assert unclassified == [], "classify these in docs/site.toml"


def test_no_document_is_both_published_and_excluded():
    assert [source for source in SOURCES if site_nav.excluded(source)] == []


def test_every_exclude_pattern_covers_a_document():
    tracked = site_nav.tracked_documents()
    stale = [
        pattern
        for pattern in site_nav.site()["exclude"]["paths"]
        if not any(site_nav._covers(pattern, path) for path in tracked)
    ]
    assert stale == []


def test_every_page_starts_with_its_title():
    """No front matter: GitHub renders the files as they are, and the site reads the title from line one."""
    assert [page.source for page in PAGES if not page.path.read_text(encoding="utf-8").startswith("# ")] == []


def test_the_base_is_a_directory_path():
    assert re.fullmatch(r"/([a-z0-9-]+/)+", site_nav.site()["base"])


def test_slugs_are_lowercase_words():
    data = site_nav.site()
    slugs = [section["slug"] for section in data["sections"]]
    slugs += [page.slug for page in PAGES if page.slug]
    slugs += [level["slug"] for level in site_nav.levels()]
    assert [slug for slug in slugs if not SLUG.fullmatch(slug)] == []


def test_section_slugs_are_unique():
    sections = [section["slug"] for section in site_nav.site()["sections"]]
    assert len(sections) == len(set(sections))


@pytest.mark.parametrize("section", [s["slug"] for s in site_nav.site()["sections"]])
def test_page_slugs_are_unique_within_a_section(section):
    slugs = [page.slug for page in PAGES if page.section == section and page.slug]
    assert [slug for slug, n in Counter(slugs).items() if n > 1] == []


def test_urls_are_unique():
    assert [url for url, n in Counter(page.url for page in PAGES).items() if n > 1] == []


def test_the_urls_follow_the_rule():
    """W2 builds URLs from this rule alone: home at the base, a section's index at its slug, a page under it."""
    base = site_nav.site()["base"]
    assert PAGES[0] == Page(source="README.md", section="", slug="", url=base)
    assert site_nav.pages()[1].url == f"{base}tutorial/"
    todo = next(page for page in PAGES if page.source == "docs/tutorials/03-todo-app.md")
    assert todo.url == f"{base}tutorial/todo-app/"


def test_every_tutorial_is_in_the_tutorial_section():
    files = sorted(
        path for path in site_nav.tracked_documents() if re.fullmatch(r"docs/tutorials/\d\d-[^/]+\.md", path)
    )
    assert files and sorted(page.source for page in site_nav.tutorials()) == files


def test_every_tutorial_has_a_level_and_a_time():
    levels = {level["slug"] for level in site_nav.levels()}
    for page in site_nav.tutorials():
        assert page.level in levels, page.source
        minutes = page.minutes
        if isinstance(minutes, tuple):
            assert len(minutes) == 2 and 0 < minutes[0] < minutes[1], page.source
        else:
            assert isinstance(minutes, int) and minutes > 0, page.source


def test_only_tutorials_have_a_level_or_a_time():
    others = [page for page in PAGES if page not in site_nav.tutorials()]
    assert [page.source for page in others if page.level or page.minutes or page.summary] == []


def test_the_reference_follows_the_feature_index():
    """docs/features/README.md is the reference's classification: its pages, in the order it first links them."""
    index = (site_nav.ROOT / "docs" / "features" / "README.md").read_text(encoding="utf-8")
    linked = list(dict.fromkeys(re.findall(r"\]\(\./([a-z0-9-]+\.md)", index)))
    reference = [page.source for page in PAGES if page.section == "reference" and page.slug]
    assert reference == [f"docs/features/{name}" for name in linked]


@pytest.mark.parametrize(
    ("minutes", "written"),
    [(30, "30분"), (60, "1시간"), (90, "1.5시간"), (180, "3시간"), ((60, 120), "1-2시간"), ((30, 90), "30분-1.5시간")],
)
def test_minutes_are_written_the_way_the_index_writes_them(minutes, written):
    assert site_nav.human_minutes(minutes) == written


# --- docs/redirects.toml ---------------------------------------------------------------------------


FILES = site_nav.files()


def _check_redirects(entries: list[dict], urls: set[str], base: str) -> list[str]:
    return site_nav.check_redirects(entries, urls, FILES, base)


def test_the_redirects_lead_to_pages():
    """A page's redirect leads to a page; a published file's (llms.txt, the skill) to a published file."""
    assert site_nav.redirect_problems() == []


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ([{"from": "/wireview/tutorial/todo/", "to": "/wireview/tutorial/todo-app/"}], None),
        ([{"from": "/wireview/tutorial/todo-app/", "to": "/wireview/tutorial/"}], "still a page"),
        ([{"from": "/wireview/tutorial/todo/", "to": "/wireview/tutorial/gone/"}], "is not a page"),
        (
            [
                {"from": "/wireview/a/", "to": "/wireview/b/"},
                {"from": "/wireview/b/", "to": "/wireview/tutorial/"},
            ],
            "a chain",
        ),
        (
            [
                {"from": "/wireview/a/", "to": "/wireview/tutorial/"},
                {"from": "/wireview/a/", "to": "/wireview/guide/"},
            ],
            "more than once",
        ),
        ([{"from": "/elsewhere/", "to": "/wireview/tutorial/"}], "not a site path"),
        ([{"from": "/wireview/agent/wireview/old.md", "to": "/wireview/agent/wireview/SKILL.md"}], None),
        ([{"from": "/wireview/llms-full.txt", "to": "/wireview/llms.txt"}], None),
        (
            [{"from": "/wireview/agent/wireview/SKILL.md", "to": "/wireview/llms.txt"}],
            "the same extension",
        ),
        ([{"from": "/wireview/agent/wireview/SKILL.md", "to": "/wireview/llms.txt"}], "still published"),
        ([{"from": "/wireview/agent/old.md", "to": "/wireview/agent/gone.md"}], "not a published file"),
        ([{"from": "/wireview/agent/old.md", "to": "/wireview/tutorial/"}], "a file to a file"),
        ([{"from": "/wireview/old/", "to": "/wireview/llms.txt"}], "a file to a file"),
        ([{"from": "/wireview/old", "to": "/wireview/tutorial/"}], "a file to a file"),
    ],
)
def test_the_redirect_rules_catch_what_they_say(entries, problem):
    problems = _check_redirects(entries, URLS, "/wireview/")
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems
