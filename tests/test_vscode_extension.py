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
from django.template import engines
from django.template.loader import get_template

import wireview
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


def _diagnose(metadata_path: Path, *targets: Path) -> dict[str, list[dict]]:
    result = subprocess.run(
        [_node(), str(DRIVER), str(metadata_path), *map(str, targets)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


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


@pytest.mark.unit
def test_the_readme_lists_every_setting_with_its_default():
    manifest = json.loads((EXTENSION / "package.json").read_text(encoding="utf-8"))
    settings = {
        name: json.dumps(spec["default"], separators=(", ", ": "))
        for name, spec in manifest["contributes"]["configuration"]["properties"].items()
    }
    readme = (EXTENSION / "README.md").read_text(encoding="utf-8")
    listed = dict(re.findall(r"^\| `(wireview\.[\w.]+)` \| `([^`]*)` \|", readme, flags=re.M))
    assert listed == settings
    # What Restricted Mode keeps to the user's own settings: every one that picks what runs or what is read
    restricted = manifest["capabilities"]["untrustedWorkspaces"]["restrictedConfigurations"]
    folders = (EXTENSION / "src" / "folders.ts").read_text(encoding="utf-8")
    source = re.search(r"SOURCE_SETTINGS = \[([^\]]*)\]", folders)
    assert source, "folders.ts names the settings that pick the metadata's source"
    assert set(restricted) == {f"wireview.{key}" for key in re.findall(r'"(\w+)"', source.group(1))}
