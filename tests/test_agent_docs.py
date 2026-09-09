"""Guard: the agent harness must not drift from the repository.

``CLAUDE.md`` and the skills under ``.claude/skills/`` are the map an agent
reads before it touches anything. They point at files, ``make`` targets and
GAP numbers instead of copying them, which keeps one source of truth but means
a rename elsewhere silently turns the map into a lie. These tests fail when
that happens.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
HARNESS_DOCS = [ROOT / "CLAUDE.md", *SKILLS]

# Generated at build or run time: referenced on purpose, absent from a clean tree.
GENERATED = {"wireview.min.js", "*.pyi", ".wireview/", "tests/static/", "*.min.js"}

# Slashes that are not directories.
NOT_PATHS = {
    "itda-work/django-wireview",  # the GitHub repo slug
    "wire-viewport-top/bottom",  # shorthand for two DOM attributes
}

# A backticked token is a path only if it says so. Without this, `WireviewMeta.send`
# reads as a file named "send".
EXTENSIONS = (
    ".md",
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".html",
    ".txt",
    ".cfg",
    ".ini",
    ".lock",
    ".d.ts",
)

FENCE = re.compile(r"^\s*```")
BACKTICKED = re.compile(r"`([^`\n]+)`")
PATH_LIKE = re.compile(r"^[\w.][\w./-]*$")
MAKE_TARGET = re.compile(r"\bmake ([a-z][a-z0-9-]*)")
GAP_REF = re.compile(r"\bGAP-(\d{3})\b")


def _outside_fences(text: str) -> str:
    """Drop fenced code blocks. The repository map is a tree, not a list of paths."""
    kept, in_fence = [], False
    for line in text.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def _referenced_paths(text: str) -> set[str]:
    found = set()
    for token in BACKTICKED.findall(_outside_fences(text)):
        token = token.strip()
        if token in GENERATED or token in NOT_PATHS or "*" in token or " " in token:
            continue
        if not PATH_LIKE.match(token):
            continue
        if "/" in token or token.endswith(EXTENSIONS):
            found.add(token)
    return found


def _doc_id(path: Path) -> str:
    return str(path.relative_to(ROOT))


@pytest.mark.unit
def test_the_harness_has_the_documents_it_claims():
    assert (ROOT / "CLAUDE.md").exists()
    assert SKILLS, "no skill found under .claude/skills/*/SKILL.md"


@pytest.mark.unit
@pytest.mark.parametrize("doc", HARNESS_DOCS, ids=_doc_id)
def test_every_referenced_path_exists(doc: Path):
    missing = sorted(p for p in _referenced_paths(doc.read_text()) if not (ROOT / p).exists())
    assert missing == [], f"{_doc_id(doc)} points at paths that do not exist: {missing}"


@pytest.mark.unit
@pytest.mark.parametrize("doc", HARNESS_DOCS, ids=_doc_id)
def test_every_make_target_exists(doc: Path):
    makefile = (ROOT / "Makefile").read_text()
    defined = set(re.findall(r"^([a-z][a-z0-9-]*):", makefile, re.MULTILINE))
    defined.update(re.findall(r"^\.PHONY:(.*)$", makefile, re.MULTILINE)[0].split())
    used = set(MAKE_TARGET.findall(doc.read_text()))
    assert used - defined == set(), f"{_doc_id(doc)} names make targets the Makefile does not define"


@pytest.mark.unit
@pytest.mark.parametrize("doc", HARNESS_DOCS, ids=_doc_id)
def test_every_gap_number_is_tracked(doc: Path):
    known = set(GAP_REF.findall((ROOT / "docs" / "FEATURE-GAP.md").read_text()))
    used = set(GAP_REF.findall(doc.read_text()))
    assert used - known == set(), f"{_doc_id(doc)} cites GAP numbers absent from docs/FEATURE-GAP.md"


@pytest.mark.unit
@pytest.mark.parametrize("skill", SKILLS, ids=_doc_id)
def test_skill_frontmatter_names_its_own_directory(skill: Path):
    text = skill.read_text()
    assert text.startswith("---\n"), f"{_doc_id(skill)} has no frontmatter"
    frontmatter = text.split("---\n", 2)[1]

    name = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    assert name, f"{_doc_id(skill)} frontmatter has no name"
    assert name.group(1) == skill.parent.name, (
        f"{_doc_id(skill)} is named {name.group(1)} but lives in {skill.parent.name}/"
    )

    description = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    assert description, f"{_doc_id(skill)} frontmatter has no description"
    assert len(description.group(1)) > 40, f"{_doc_id(skill)} description is too short to route on"


# --- documentation language -----------------------------------------------------------------

#: Prose is Korean; code, identifiers and commit messages are English (CLAUDE.md "규약").
#: Measured on prose only -- code fences and backticked spans are dropped first -- so a page
#: that is mostly tables of API names still passes. The lowest real document sits at 22%.
KOREAN_RATIO_FLOOR = 0.15

#: English on purpose. The changelog reads alongside commit messages, and docs/legacy/ is a
#: preserved artefact of the django-reactor era, not a document this project maintains.
ENGLISH_BY_DESIGN = ("CHANGELOG.md", "docs/legacy/")

HANGUL = re.compile(r"[가-힣]")
LETTERS = re.compile(r"[A-Za-z가-힣]")
CODE_SPAN = re.compile(r"`[^`\n]*`")
CODE_FENCE = re.compile(r"```.*?```", re.S)


def _tracked_markdown() -> list[str]:
    import subprocess

    listed = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [
        path
        for path in listed.stdout.split()
        if not path.startswith(ENGLISH_BY_DESIGN) and path not in ENGLISH_BY_DESIGN
    ]


def _korean_ratio(text: str) -> tuple[float, int]:
    prose = CODE_SPAN.sub("", CODE_FENCE.sub("", text))
    letters = LETTERS.findall(prose)
    if not letters:
        return 1.0, 0
    return len(HANGUL.findall(prose)) / len(letters), len(letters)


@pytest.mark.unit
def test_the_documentation_is_written_in_korean():
    """Guard: prose in tracked Markdown stays Korean.

    The split is deliberate -- Korean for anything a reader reads, English for
    anything a machine or a git log reads -- and it drifts silently, one new
    English page at a time, because nothing about an English document looks
    broken.
    """
    english = []
    for path in _tracked_markdown():
        ratio, letters = _korean_ratio((ROOT / path).read_text(encoding="utf-8"))
        if letters >= 200 and ratio < KOREAN_RATIO_FLOOR:
            english.append(f"{path} ({ratio:.0%} Korean)")

    assert english == [], "documents whose prose is not Korean: " + ", ".join(english)
