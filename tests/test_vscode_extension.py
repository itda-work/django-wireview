"""What the editor extension (editors/vscode) holds against the library (#156).

The extension reads the project through ``manage.py wireview_lsp`` and keeps a
few tables of its own: the tag snippets, the HTML attributes it describes, the
block tags its indentation rules name, the metadata version it accepts. Each
table here is held against the library it describes, so a tag renamed in one
place and not the other fails here rather than in someone's editor.

The strongest check is the first: the extension's diagnostics run over every
template in this repository and find nothing. Its rule is to speak only of
what Django or django-wireview would raise, and a template that renders is the
proof of the other half -- a rule that flags one is wrong. It runs the
extension's own code under node (scripts/diagnose.ts imports only the core
modules, which import only node's), so it needs node and no npm packages.

The render-part SQL the dev server writes (#188) is held the same way: the
extension's reader runs on the files the library really wrote, through
test/queries-driver.ts, at the end of this module.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import django
import pytest
from asgiref.sync import sync_to_async
from django.template import engines
from django.template.loader import get_template
from testproj.wireview_setting import set_wireview

import wireview
from examples.quiz.models import Choice, Question, Quiz
from wireview import Component, mount
from wireview.debug import render_queries, render_queries_file
from wireview.management.commands.wireview_lsp import METADATA_VERSION, extract_metadata

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "editors" / "vscode"
DRIVER = EXTENSION / "scripts" / "diagnose.ts"
#: Where the repository keeps templates the test project's loaders find.
REPOSITORY_TEMPLATES = [ROOT / "examples", ROOT / "tests" / "testproj", ROOT / "wireview" / "templates"]
STARTER = Path(wireview.__file__).parent / "project_template"


def _node() -> str:
    node = shutil.which("node")
    # Not a skip: node already builds the client bundle, and a skipped check here
    # would leave the extension's rules unchecked against the templates
    assert node, "node is required: editors/vscode/scripts/diagnose.ts runs the extension's diagnostics"
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    major, minor = (int(part) for part in version.lstrip("v").split(".")[:2])
    # Type stripping without a flag: node runs the .ts sources as they are
    assert (major, minor) >= (22, 18), f"node {version} cannot run TypeScript as it is; 22.18 or later can"
    return node


def _diagnose(metadata_path: Path, *targets: Path, strict: bool = False) -> dict[str, list[dict]]:
    result = subprocess.run(
        [_node(), str(DRIVER), *(["--strict"] if strict else []), str(metadata_path), *map(str, targets)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    report = json.loads(result.stdout)
    # The exit status is the gate a CI reads (#179): 1 exactly when an error is
    # reported, or with --strict a warning
    failing = {"error", "warning"} if strict else {"error"}
    found = any(p["severity"] in failing for problems in report.values() for p in problems)
    assert result.returncode == int(found), (result.returncode, _findings(report))
    return report


def _findings(report: dict[str, list[dict]]) -> list[str]:
    def shown(path: str) -> str:
        return str(Path(path).relative_to(ROOT)) if Path(path).is_relative_to(ROOT) else path

    return [
        f"{shown(path)}:{p['line']}: {p['code']}: {p['message']}" for path, problems in report.items() for p in problems
    ]


@pytest.fixture(scope="module")
def metadata_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("lsp") / "metadata.json"
    path.write_text(json.dumps(extract_metadata()), encoding="utf-8")
    return path


@pytest.mark.integration
def test_the_repository_templates_have_nothing_to_report(metadata_file):
    report = _diagnose(metadata_file, *REPOSITORY_TEMPLATES)
    assert len(report) > 100, "the driver did not find the templates"
    assert _findings(report) == []


@pytest.mark.integration
def test_the_driver_reports_what_is_wrong(metadata_file, tmp_path):
    """The check above passes on an empty answer too; this is the other half."""
    broken = tmp_path / "broken.html"
    broken.write_text(
        '{% load wireview %}{% component "NoSuchComponent" %}{% if a %}{{ x|no_such_filter }}',
        encoding="utf-8",
    )
    codes = sorted(p["code"] for p in _diagnose(metadata_file, broken)[str(broken)])
    assert codes == ["unclosed-block", "unknown-component", "unknown-filter"]


@pytest.mark.integration
def test_a_warning_fails_the_driver_only_when_strict(metadata_file, tmp_path):
    """A warning may be fine (another library's tag), so it is a failure only on request (#179)."""
    page = tmp_path / "page.html"
    page.write_text("{% no_such_tag %}", encoding="utf-8")
    assert [p["severity"] for p in _diagnose(metadata_file, page)[str(page)]] == ["warning"]
    assert [p["severity"] for p in _diagnose(metadata_file, page, strict=True)[str(page)]] == ["warning"]


@pytest.mark.integration
@pytest.mark.parametrize("args", [[], ["--strict"], ["--strict", "metadata.json"], ["--strict", "--strict", "x"]])
def test_the_driver_exits_2_on_a_usage_error(args):
    result = subprocess.run([_node(), str(DRIVER), *args], capture_output=True, text=True, timeout=60, check=False)
    assert (result.returncode, result.stdout) == (2, "")
    assert result.stderr.startswith("usage:")


@pytest.mark.integration
def test_a_linked_template_is_checked_against_its_component(tmp_path):
    """The editor and the metadata name a linked template by the same path, so its handlers are checked."""
    from wireview.management.commands.wireview_lsp import find_template

    root = tmp_path / "templates"
    root.mkdir()
    real = tmp_path / "elsewhere.html"
    real.write_text('{% load wireview %}<b {% on "click" "no_such_handler" %}>', encoding="utf-8")
    (root / "linked.html").symlink_to(real)
    metadata = extract_metadata()
    component = {**metadata["components"]["XTodoList"], "template_path": find_template("linked.html", [root])}
    metadata["components"] = {"XTodoList": component}
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    report = _diagnose(metadata_path, root / "linked.html")
    assert [p["code"] for p in report[str(root / "linked.html")]] == ["unknown-handler"]


def _as_written_by_1_1(metadata: dict) -> dict:
    """What a django-wireview before #162 wrote: every method on every component, nothing at the top."""
    described = metadata.pop("framework_methods")
    for component in metadata["components"].values():
        inherited = component.pop("inherited_methods")
        methods = {
            name: {"is_handler": False, **described[owner][name]}
            for owner, names in inherited.items()
            for name in names
        }
        component["methods"] = dict(sorted({**methods, **component["methods"]}.items()))
    return {**metadata, "version": "1.1"}


@pytest.mark.integration
@pytest.mark.parametrize("shape", ["2.0", "1.1"])
def test_a_framework_method_is_not_a_handler_in_either_shape(tmp_path, shape):
    """2.0 names the framework's methods on a component and describes them once at the top (#162).

    The extension puts them back on the component, so a framework name in
    ``{% on %}`` is still "not a handler" rather than "no such method", and it
    reads a project whose django-wireview still writes 1.1 alike.
    """
    from wireview.management.commands.wireview_lsp import find_template

    root = tmp_path / "templates"
    root.mkdir()
    page = root / "page.html"
    page.write_text(
        '{% load wireview %}<b {% on "click" "model_dump" %}></b><b {% on "click" "destroy" %}></b>'
        '<b {% on "click" "mutation" %}></b><b {% on "click" "no_such_handler" %}></b><b {% on "click" "add" %}></b>',
        encoding="utf-8",
    )
    metadata = extract_metadata()
    assert metadata["version"] == "2.0"
    if shape == "1.1":
        metadata = _as_written_by_1_1(metadata)
    component = {**metadata["components"]["XTodoList"], "template_path": find_template("page.html", [root])}
    # model_dump and destroy are the framework's; mutation is XTodoList's own override of a framework name
    assert component["methods"]["add"]["is_handler"] and not component["methods"]["mutation"]["is_handler"]
    metadata["components"] = {"XTodoList": component}
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    report = _diagnose(metadata_path, page)
    assert [p["code"] for p in report[str(page)]] == ["not-a-handler"] * 3 + ["unknown-handler"]


@pytest.mark.integration
def test_the_filter_tag_is_checked_as_the_engine_has_the_filters(tmp_path):
    """Django refuses {% filter safe %} by the function's _filter_name: a project that renames it is not refused."""
    from django.template import Library
    from django.template.defaultfilters import safe

    page = tmp_path / "page.html"
    page.write_text("{% filter safe %}x{% endfilter %}", encoding="utf-8")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(extract_metadata()), encoding="utf-8")
    assert [p["code"] for p in _diagnose(metadata_path, page)[str(page)]] == ["filter-not-permitted"]

    try:
        Library().filter("okay", safe)
        assert engines["django"].from_string(page.read_text()).render({}) == "x"
        metadata_path.write_text(json.dumps(extract_metadata()), encoding="utf-8")
        assert _diagnose(metadata_path, page)[str(page)] == []
    finally:
        safe._filter_name = "safe"


@pytest.mark.integration
def test_the_starter_project_templates_have_nothing_to_report(tmp_path):
    """The starter's components are not the test project's: its own manage.py describes them."""
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False
        )

    made = run("-m", "django", "startproject", "mysite", str(tmp_path), "--template", str(STARTER))
    assert made.returncode == 0, made.stderr
    written = run("manage.py", "wireview_lsp", "--output", "metadata.json")
    assert written.returncode == 0, written.stderr
    report = _diagnose(tmp_path / "metadata.json", tmp_path / "hello" / "templates")
    assert len(report) == len(list((STARTER / "hello" / "templates").rglob("*.html")))
    assert _findings(report) == []


@pytest.mark.unit
def test_every_repository_template_compiles():
    """What the diagnostics stand in for, asked of Django itself.

    ``streams/stream_list.html`` named a tag that does not exist and never
    compiled; the tests that named it never rendered it. The extension's
    diagnostics found it.
    """
    backend = engines["django"]
    roots = [Path(d) for d in backend.engine.dirs] + [
        Path(config.path) / "templates" for config in django.apps.apps.get_app_configs()
    ]
    failures = []
    for root in roots:
        if not root.is_dir() or not root.resolve().is_relative_to(ROOT) or ".venv" in root.parts:
            continue
        for path in sorted(root.rglob("*.html")):
            name = path.relative_to(root).as_posix()
            try:
                get_template(name)
            except Exception as error:  # noqa: BLE001 - the failure is the finding
                failures.append(f"{path.relative_to(ROOT)}: {type(error).__name__}: {error}")
    assert failures == []


def _metadata() -> dict:
    return extract_metadata()


@pytest.mark.unit
def test_the_tag_snippets_name_real_tags():
    snippets = json.loads((EXTENSION / "data" / "tag-snippets.json").read_text(encoding="utf-8"))
    metadata = _metadata()
    known = set(metadata["template_builtins"]["tags"])
    for library in metadata["template_libraries"].values():
        known |= set(library["tags"])
    assert sorted(set(snippets) - known) == []
    # Every wireview tag has one: the extension's completion inserts the whole tag
    assert sorted(set(metadata["template_libraries"]["wireview"]["tags"]) - set(snippets)) == []
    for name, body in snippets.items():
        assert body.split(" ", 1)[0] == name, f"{name}'s snippet starts with another tag: {body}"


@pytest.mark.unit
def test_the_html_attributes_are_ones_the_client_reads():
    data = json.loads((EXTENSION / "data" / "html-data.json").read_text(encoding="utf-8"))
    sources = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "wireview" / "static" / "wireview").iterdir())
        if path.suffix in {".js", ".mjs"} and not path.name.endswith(".min.js")
    )
    described = [attribute["name"] for attribute in data["globalAttributes"]]
    assert described, "no attributes"
    assert [name for name in described if name not in sources] == []
    # The ones a template writes by hand. Tags write wire-upload*, wire-preview and wire-on-*,
    # the server writes wire-join-failed and wire-sticky: not for the editor to offer
    hand_written = {
        "wire-hook",
        "wire-update",
        "wire-boost",
        "wire-stream",
        "wire-viewport-top",
        "wire-viewport-bottom",
        "wire-disabled-with",
        "wire-feedback-for",
        "wire-auto-recover",
        "wire-flash",
    }
    assert set(described) == hand_written


@pytest.mark.unit
def test_the_extension_reads_the_metadata_version_this_library_writes():
    source = (EXTENSION / "src" / "core" / "metadata.ts").read_text(encoding="utf-8")
    major = re.search(r"export const METADATA_MAJOR = (\d+);", source)
    minor = re.search(r"export const METADATA_MINOR = (\d+);", source)
    assert major and minor
    assert METADATA_VERSION == f"{major[1]}.{minor[1]}"


@pytest.mark.unit
def test_the_indentation_rules_name_real_block_tags():
    config = json.loads((EXTENSION / "language-configuration.json").read_text(encoding="utf-8"))
    metadata = _metadata()
    blocks: set[str] = set()
    middles: set[str] = set()
    for library in [metadata["template_builtins"], *metadata["template_libraries"].values()]:
        for name, tag in library["tags"].items():
            if tag["end"]:
                blocks.add(name)
                middles |= set(tag["intermediate"])
    if django.VERSION < (6, 0):
        blocks.add("partialdef")  # Django 6.0's: the rule is for whoever has it
    patterns = [config["indentationRules"]["increaseIndentPattern"], config["onEnterRules"][2]["beforeText"]]
    for pattern in patterns:
        names = re.search(r"\\\{%\\s\*\(\?:([a-z_|]+)\)", pattern)
        assert names, pattern
        assert sorted(set(names[1].split("|")) - blocks - middles) == []
    # And each tag that opens a block opens it in the rules
    increase = re.search(r"\\\{%\\s\*\(\?:([a-z_|]+)\)", patterns[0])[1].split("|")  # type: ignore[index]
    wireview_blocks = {name for name, tag in metadata["template_libraries"]["wireview"]["tags"].items() if tag["end"]}
    assert sorted(wireview_blocks - set(increase)) == []


def _expand(body: str) -> str:
    """A snippet as it reads once every stop holds its default: `$4` repeats what `${4:count}` holds."""
    defaults: dict[str, str] = {}
    for number, text in re.findall(r"\$\{(\d+):([^${}]*)\}", body):
        defaults.setdefault(number, text)
    defaults.setdefault("0", "")  # where the cursor ends
    previous = None
    while previous != body:
        previous = body
        body = re.sub(r"\$(\d+)(?![\d:|])", lambda match: defaults.get(match[1], "x"), body)
        body = re.sub(r"\$\{\d+:([^${}]*)\}", r"\1", body)
        body = re.sub(r"\$\{\d+\|([^,|]*)[^}]*\|\}", r"\1", body)
    return body.replace("\t", "    ")


@pytest.mark.unit
def test_the_python_snippets_are_python_with_async_handlers():
    snippets = json.loads((EXTENSION / "snippets" / "python.json").read_text(encoding="utf-8"))
    for name, snippet in snippets.items():
        source = _expand("\n".join(snippet["body"]))
        tree = ast.parse(source, filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    assert not isinstance(item, ast.FunctionDef), f"{name}: {item.name} is not async"
            if isinstance(node, ast.FunctionDef) and node.args.args[:1] and node.args.args[0].arg == "self":
                pytest.fail(f"{name}: {node.name} is not async")
        assert "f'" not in source and 'f"' not in source, f"{name}: markup from an f-string is not escaped"


@pytest.mark.unit
def test_only_the_host_tests_are_outside_the_release_gate():
    """release.yml calls ci.yml as its gate: the extension's checks and package are in it, VS Code downloads are not."""
    import yaml

    workflows = ROOT / ".github" / "workflows"
    ci = yaml.safe_load((workflows / "ci.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load((workflows / "release.yml").read_text(encoding="utf-8"))
    assert release["jobs"]["ci"]["with"] == {"release": True}
    skipped = {name for name, job in ci["jobs"].items() if "inputs.release" in str(job.get("if", ""))}
    assert skipped == {"vscode-extension-host"}
    host = " ".join(str(step.get("run", "")) for step in ci["jobs"]["vscode-extension-host"]["steps"])
    gate = " ".join(str(step.get("run", "")) for step in ci["jobs"]["vscode-extension"]["steps"])
    assert "test:host" in host and "test:host" not in gate
    assert all(command in gate for command in ("npm run typecheck", "npm test", "npm run package"))


#: The Marketplace page (English) and its Korean text (#163)
READMES = ("README.md", "README.ko.md")


@pytest.mark.unit
def test_the_readmes_list_every_diagnostic_and_command():
    """The two READMEs are kept by hand side by side; the tables they share are held against the code."""
    diagnostics = (EXTENSION / "src" / "core" / "diagnostics.ts").read_text(encoding="utf-8")
    codes = set(re.findall(r'report\(\s*"([a-z]+(?:-[a-z]+)+)"', diagnostics))
    manifest = json.loads((EXTENSION / "package.json").read_text(encoding="utf-8"))
    commands = {f"{c['category']}: {c['title']}" for c in manifest["contributes"]["commands"]}
    assert len(codes) > 20, "the diagnostic codes are no longer read from diagnostics.ts"
    for name in READMES:
        readme = (EXTENSION / name).read_text(encoding="utf-8")
        rows = [line for line in readme.splitlines() if line.startswith("| `") and "-" in line.split("|")[1]]
        listed = {code for row in rows for code in re.findall(r"`([a-z]+(?:-[a-z]+)+)`", row.split("|")[1])}
        assert listed == codes, name
        assert all(f"`{command}`" in readme for command in commands), name
    english = (EXTENSION / "README.md").read_text(encoding="utf-8")
    assert "[README.ko.md](./README.ko.md)" in english
    assert "[README.md](./README.md)" in (EXTENSION / "README.ko.md").read_text(encoding="utf-8")


@pytest.mark.unit
def test_the_readme_lists_every_setting_with_its_default():
    manifest = json.loads((EXTENSION / "package.json").read_text(encoding="utf-8"))
    settings = {
        name: json.dumps(spec["default"], separators=(", ", ": "))
        for name, spec in manifest["contributes"]["configuration"]["properties"].items()
    }
    for name in READMES:
        readme = (EXTENSION / name).read_text(encoding="utf-8")
        listed = dict(re.findall(r"^\| `(wireview\.[\w.]+)` \| `([^`]*)` \|", readme, flags=re.M))
        assert listed == settings, name
    # What Restricted Mode keeps to the user's own settings: every one that picks what runs or what is read
    restricted = manifest["capabilities"]["untrustedWorkspaces"]["restrictedConfigurations"]
    folders = (EXTENSION / "src" / "folders.ts").read_text(encoding="utf-8")
    source = re.search(r"SOURCE_SETTINGS = \[([^\]]*)\]", folders)
    assert source, "folders.ts names the settings that pick the metadata's source"
    assert set(restricted) == {f"wireview.{key}" for key in re.findall(r'"([\w.]+)"', source.group(1))}


@pytest.mark.unit
def test_the_extension_id_is_the_publishers():
    """The Marketplace publisher is ``itda``; the host tests look the extension up by its id."""
    manifest = json.loads((EXTENSION / "package.json").read_text(encoding="utf-8"))
    extension_id = f"{manifest['publisher']}.{manifest['name']}"
    host = [path.read_text(encoding="utf-8") for path in (EXTENSION / "test" / "host").glob("*.cjs")]

    assert extension_id == "itda.django-wireview"
    assert set(re.findall(r'getExtension\("([^"]+)"\)', " ".join(host))) == {extension_id}


@pytest.mark.unit
def test_the_icon_is_a_square_png_the_package_carries():
    """The Marketplace takes a PNG of 128px or more. images/icon.svg is its source."""
    import struct

    manifest = json.loads((EXTENSION / "package.json").read_text(encoding="utf-8"))
    icon = EXTENSION / manifest["icon"]
    head = icon.read_bytes()[:24]
    width, height = struct.unpack(">II", head[16:24])

    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    assert width == height >= 128
    assert icon.with_suffix(".svg").is_file()
    assert f"!{manifest['icon']}" in (EXTENSION / ".vscodeignore").read_text(encoding="utf-8").splitlines()


# -- render-part SQL: what the library writes is what the extension reads (#188) -----------------
#
# The server's records (wireview/debug/render_queries_file.py) are read by the
# extension's own code (editors/vscode/src/core/queries.ts) through
# test/queries-driver.ts, as an editor that has just started reads them: the
# scenarios of docs/design/render-queries-editor.md §6-2.

QUERIES_DRIVER = EXTENSION / "test" / "queries-driver.ts"

VSX_TEMPLATES = {
    "vsx/plain.html": "{% load wireview %}<p {% tag_header %}>\n{{ choices.count }}\n</p>",
    "vsx/shelf.html": '{% load wireview %}<main {% tag_header %}>\n{% component "VsxBook" id="book" %}\n</main>',
    "vsx/book.html": (
        "{% load wireview %}<ol {% tag_header %}>\n{% for c in choices %}{{ c.question.text }}{% endfor %}\n</ol>"
    ),
    "vsx/props.html": "{% load wireview %}<p {% tag_header %}></p>",
}


class VsxPlain(Component):
    class Meta:
        template_name = "vsx/plain.html"

    @property
    def choices(self):
        return Choice.objects.all()


class VsxShelf(Component):
    class Meta:
        template_name = "vsx/shelf.html"


class VsxBook(Component):
    class Meta:
        template_name = "vsx/book.html"

    empty: bool = False

    @property
    def choices(self):
        return Choice.objects.none() if self.empty else Choice.objects.all()


class VsxProps(Component):
    class Meta:
        template_name = "vsx/props.html"

    @property
    def total(self) -> int:
        return Choice.objects.count()


@pytest.fixture
def vsx_templates(tmp_path: Path, settings) -> Path:
    root = tmp_path / "templates"
    for name, text in VSX_TEMPLATES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(root)],
            "OPTIONS": {
                "loaders": [("django.template.loaders.cached.Loader", ["django.template.loaders.filesystem.Loader"])]
            },
        }
    ]
    return root


@pytest.fixture
def records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings, vsx_templates: Path):
    """Writing on, as on a dev server, to a directory of the test's."""
    out = tmp_path / "render-queries"
    monkeypatch.delenv(render_queries_file.ENVIRONMENT, raising=False)
    monkeypatch.setattr(render_queries_file, "_suppressed", False)
    settings.DEBUG = True
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=True, DEBUG_RENDER_QUERIES_DIR=str(out))
    render_queries_file._reset()
    yield out
    render_queries_file._reset()


@pytest.fixture
def quiz(db):
    quiz = Quiz.objects.create(title="Q")
    for n in range(3):
        Choice.objects.create(question=Question.objects.create(quiz=quiz, text=f"q{n}"), text=f"c{n}")
    return quiz


def _hints(directory: Path, *documents: tuple[Path, str]) -> dict[Path, dict[int, str]]:
    """What the extension would show on each document: {line: label}."""
    spec = directory.parent / "spec.json"
    spec.write_text(
        json.dumps({"directory": str(directory), "documents": [{"path": str(p), "kind": k} for p, k in documents]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [_node(), str(QUERIES_DRIVER), str(spec)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert answer["refused"] == [] and answer["overlapping"] is None, answer
    return {Path(path): {hint["line"]: hint["label"] for hint in hints} for path, hints in answer["hints"].items()}


def _reset_loaders() -> None:
    """What the autoreloader's template_changed does."""
    for loader in engines["django"].engine.template_loaders:
        loader.reset()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_extension_tells_the_line_the_server_ran_and_hides_a_compile_the_cache_still_runs(
    records, quiz, vsx_templates
):
    plain = vsx_templates / "vsx/plain.html"
    view = await mount(VsxPlain, id="plain")
    await sync_to_async(view.render)()
    assert _hints(records, (plain, "template")) == {plain: {2: "1 query"}}

    # Another line changes on disk; the cached compile is what runs, and its digest is the old file's
    plain.write_text(plain.read_text().replace("<p ", "<p data-new ", 1), encoding="utf-8")
    await sync_to_async(view.render)()
    assert _hints(records, (plain, "template")) == {plain: {}}

    _reset_loaders()
    view = await mount(VsxPlain, id="plain")
    await sync_to_async(view.render)()
    assert _hints(records, (plain, "template")) == {plain: {2: "1 query"}}


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_component_fixed_alone_clears_what_it_ran_inside_another_in_the_editor(records, quiz, vsx_templates):
    """B inside A runs 4 (a loop and a repeat of 3), B alone the same, then B alone none: the line ends at nothing."""
    book = vsx_templates / "vsx/book.html"
    shelf = await mount(VsxShelf, id="shelf")
    await sync_to_async(shelf.render)()
    assert _hints(records, (book, "template")) == {book: {2: "⚠ 3× same query · 4 queries"}}
    alone = await mount(VsxBook, id="book")
    await sync_to_async(alone.render)()
    assert _hints(records, (book, "template")) == {book: {2: "⚠ 3× same query · 4 queries"}}, "replaced, not added"
    fixed = await mount(VsxBook, id="book", empty=True)
    await sync_to_async(fixed.render)()
    assert _hints(records, (book, "template")) == {book: {}}


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_property_of_an_unchanged_module_is_told_at_its_def_line_by_its_exact_stat(records, quiz):
    view = await mount(VsxProps, id="props")
    await view.render_diff()
    [record] = [json.loads(line) for path in records.glob("*.jsonl") for line in path.read_text().splitlines()]
    [prop] = [row["property"] for row in record["rows"] if "property" in row]
    assert int(prop["stat"][0]) > 2**53, "past what a JSON number holds: read as a string, compared exactly"
    here = Path(__file__).resolve()
    lines = here.read_text().splitlines()
    line = next(n for n, text in enumerate(lines, 1) if text.strip() == "def total(self) -> int:")
    assert prop["line"] == line
    assert _hints(records, (here, "python")) == {here: {line: "1 query per render"}}


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_new_process_whose_file_is_one_line_of_none_clears_the_number_of_the_one_before(
    records, quiz, vsx_templates
):
    """2판 리뷰 2: an editor reading a one-line file takes that line, and a restart is not an overlap."""
    book = vsx_templates / "vsx/book.html"
    view = await mount(VsxBook, id="book")
    await sync_to_async(view.render)()
    # What a process before this one wrote: the same lines under another process's name
    [written] = list(records.glob("*.jsonl"))
    written.rename(records / "20200101T000000-1.1.jsonl")
    assert _hints(records, (book, "template")) == {book: {2: "⚠ 3× same query · 4 queries"}}

    render_queries_file._reset()  # a new process: it has published nothing yet
    view = await mount(VsxBook, id="book", empty=True)
    await sync_to_async(view.render)()
    [fresh] = list(records.glob(f"{written.name.split('.', 1)[0]}.*.jsonl"))
    assert len(fresh.read_text().splitlines()) == 1
    assert _hints(records, (book, "template")) == {book: {}}


def _stand_in(qualname: str, id: str) -> object:
    """What a render scope reads of a component: its type, ``_name`` and ``id``."""
    item = type(qualname, (), {"__module__": __name__, "__qualname__": qualname})()
    item._name = qualname  # type: ignore[attr-defined]
    item.id = id  # type: ignore[attr-defined]
    return item


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_class_past_the_render_limit_behind_other_classes_loses_its_old_count_in_the_editor(
    records, quiz, vsx_templates
):
    """Review B1 P2-1: other classes fill the kept renders, the book's render is cut; its old count must not stay."""
    from django.db import connection

    book = vsx_templates / "vsx/book.html"
    view = await mount(VsxBook, id="book")
    await sync_to_async(view.render)()
    assert _hints(records, (book, "template")) == {book: {2: "⚠ 3× same query · 4 queries"}}

    def crowded() -> None:
        with render_queries.scope("render", _stand_in("VsxFiller", "host"), "http"):
            for n in range(render_queries_file.RENDERS_LIMIT - 1):
                with render_queries.scope("render", _stand_in("VsxFiller", f"f{n}"), "nested"):
                    pass
            with render_queries.scope("render", _stand_in("VsxBook", "book"), "nested"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")

    await sync_to_async(crowded)()
    last = [json.loads(line) for path in records.glob("*.jsonl") for line in path.read_text().splitlines()][-1]
    fqn = f"{__name__}.VsxBook"
    assert last["renders_more"] == 1 and fqn not in {render["component"] for render in last["renders"]}
    assert fqn in last["partial"]
    assert _hints(records, (book, "template")) == {book: {}}
