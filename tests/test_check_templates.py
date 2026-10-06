"""``manage.py wireview_check_templates``: the editor's diagnostics as a gate (#179).

The rules themselves are tests/test_vscode_extension.py's; this holds what the
command adds -- the exit status a CI reads, which templates it checks when told
none, the node it refuses, and the copy of the diagnostics the wheel ships, which
has to run where an installed package sits rather than in this checkout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import types
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from wireview.management.commands import wireview_check_templates as command
from wireview.management.commands.wireview_lsp import extract_metadata, template_roots

ROOT = Path(__file__).resolve().parent.parent


def _check(*args: str) -> tuple[int, str]:
    out = StringIO()
    try:
        call_command("wireview_check_templates", *args, stdout=out)
    except CommandError as error:
        return error.returncode, out.getvalue()
    return 0, out.getvalue()


def _page(tmp_path: Path, text: str, name: str = "page.html") -> Path:
    page = tmp_path / name
    page.write_text(text, encoding="utf-8")
    return page


@pytest.mark.integration
def test_a_clean_template_passes(tmp_path):
    page = _page(tmp_path, "{% load wireview %}{{ x|upper }}{% if a %}{% endif %}")
    assert _check(str(page)) == (0, "No problems in 1 template\n")


@pytest.mark.integration
def test_an_error_fails_with_where_it_is(tmp_path):
    page = _page(tmp_path, "{% load wireview %}\n{{ x|no_such_filter }}")
    status, out = _check(str(page))
    assert status == 1
    assert f"{page}:2:6: error [unknown-filter]" in out
    assert out.endswith("1 error in 1 template\n")


@pytest.mark.integration
def test_a_warning_fails_only_when_strict(tmp_path):
    """A warning may be fine -- another library's tag -- so it is a failure only on request."""
    page = _page(tmp_path, "{% no_such_tag %}")
    status, out = _check(str(page))
    assert status == 0
    assert "warning [unknown-tag]" in out
    assert out.endswith("1 warning in 1 template\n")
    assert _check(str(page), "--strict")[0] == 1


@pytest.mark.integration
def test_a_directory_is_searched_and_json_is_the_report(tmp_path):
    _page(tmp_path, "{% if a %}", "a.html")
    (tmp_path / "sub").mkdir()
    _page(tmp_path / "sub", "fine", "b.html")
    status, out = _check(str(tmp_path), "--format", "json")
    assert status == 1
    report = json.loads(out)
    assert sorted(report) == [str(tmp_path / "a.html"), str(tmp_path / "sub" / "b.html")]
    assert [p["code"] for p in report[str(tmp_path / "a.html")]] == ["unclosed-block"]
    assert report[str(tmp_path / "sub" / "b.html")] == []


@pytest.mark.integration
def test_without_paths_it_checks_the_project_and_not_installed_packages():
    """Django's own templates are Django's to check; the project's are the ones a CI gates."""
    import django

    admin = (Path(django.__file__).parent / "contrib" / "admin" / "templates").resolve()
    every = template_roots()
    assert admin in every, "the test project lost the admin, and with it an installed package's templates"
    roots = command.project_template_roots()
    assert admin not in roots
    assert (ROOT / "examples" / "todo" / "templates").resolve() in roots
    assert (ROOT / "wireview" / "templates").resolve() in roots  # this checkout's editable install
    status, out = _check()
    assert status == 0, out
    assert out.startswith("No problems in ")


@pytest.mark.unit
def test_a_package_outside_the_interpreters_site_packages_is_still_installed(tmp_path):
    """``uv run --with`` and a ``.pth`` put packages in a site-packages the interpreter does not list (#179)."""
    own = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    layered = tmp_path / "cache" / "env" / "lib" / "python3.12" / "site-packages" / "django" / "templates"
    debian = Path("/usr/lib/python3/dist-packages/someapp/templates")
    project = tmp_path / "project" / "myapp" / "templates"
    assert command.is_installed(own / "app" / "templates", [own])
    assert command.is_installed(layered, [own])
    assert command.is_installed(debian, [own])
    assert not command.is_installed(project, [own])


@pytest.mark.integration
def test_a_missing_path_cannot_run(tmp_path):
    assert _check(str(tmp_path / "nowhere"))[0] == 2


@pytest.mark.integration
def test_a_directory_without_templates_cannot_run(tmp_path):
    """A gate pointed at the wrong directory would otherwise pass every time."""
    _page(tmp_path, "not a template", "notes.txt")
    with pytest.raises(CommandError, match="no .html templates") as raised:
        call_command("wireview_check_templates", str(tmp_path))
    assert raised.value.returncode == 2


@pytest.mark.unit
def test_a_crash_in_the_diagnostics_is_not_a_finding(tmp_path):
    """node exits 1 on an uncaught error too; only a report means the templates were checked."""
    fake = tmp_path / "node"
    fake.write_text('#!/bin/sh\n[ "$1" = --version ] && echo v24.0.0 && exit 0\necho boom >&2\nexit 1\n')
    fake.chmod(0o755)
    _page(tmp_path, "fine")
    with pytest.raises(CommandError, match="the diagnostics failed:\nboom") as raised:
        call_command("wireview_check_templates", str(tmp_path), "--node", str(fake))
    assert raised.value.returncode == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("said", "message"),
    [("v22.17.1", "node 22.17 cannot run the diagnostics; 22.18 or later can"), ("garbage", "not a node version")],
)
def test_a_node_that_cannot_run_the_diagnostics_is_refused(tmp_path, said, message):
    """Exit 2, not 1: a CI must not read "node is too old" as "the templates are wrong"."""
    fake = tmp_path / "node"
    fake.write_text(f"#!/bin/sh\necho {said}\n", encoding="utf-8")
    fake.chmod(0o755)
    with pytest.raises(CommandError, match=message) as raised:
        call_command("wireview_check_templates", str(tmp_path), "--node", str(fake))
    assert raised.value.returncode == 2


@pytest.mark.unit
def test_no_node_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(CommandError, match="node is required") as raised:
        call_command("wireview_check_templates", str(tmp_path))
    assert raised.value.returncode == 2


@pytest.mark.integration
def test_the_wheel_ships_diagnostics_that_run_where_a_package_is_installed(tmp_path):
    """The wheel's copy runs outside this checkout, under whatever package.json is above it.

    An installed project's own package.json may say CommonJS; the shipped one has to
    win, or node reads the core modules' imports as a syntax error.
    """
    pytest.importorskip("hatchling")
    import hatch_build

    hook = hatch_build.CustomBuildHook(
        str(ROOT), {}, None, types.SimpleNamespace(version="1.2.3"), str(tmp_path), "wheel"
    )
    build_data: dict = {"force_include": {}}
    hook.initialize("standard", build_data)
    try:
        (source,) = [
            s for s, target in build_data["force_include"].items() if target == "wireview/template_diagnostics"
        ]
        site_packages = tmp_path / "project" / "site-packages"
        (tmp_path / "project" / "package.json").parent.mkdir()
        (tmp_path / "project" / "package.json").write_text('{"type": "commonjs"}', encoding="utf-8")
        shipped = site_packages / "wireview" / "template_diagnostics"
        shutil.copytree(source, shipped)
    finally:
        hook.finalize("standard", build_data, "")

    core = ROOT / "editors" / "vscode" / "src" / "core"
    assert sorted(p.name for p in (shipped / "src" / "core").iterdir()) == sorted(p.name for p in core.glob("*.ts"))
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps(extract_metadata()), encoding="utf-8")
    page = _page(tmp_path, "{% if a %}")
    result = subprocess.run(
        [command.find_node(None), str(shipped / "scripts" / "diagnose.ts"), str(metadata), str(page)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    assert [p["code"] for p in json.loads(result.stdout)[str(page)]] == ["unclosed-block"]


@pytest.mark.unit
def test_the_sdist_carries_what_the_hook_ships():
    """The wheel is built from the sdist, so the diagnostics have to be in it."""
    hatchling_sdist = pytest.importorskip("hatchling.builders.sdist")

    selected = {f.relative_path for f in hatchling_sdist.SdistBuilder(str(ROOT)).recurse_included_files()}

    assert "editors/vscode/scripts/diagnose.ts" in selected
    assert "editors/vscode/src/core/diagnostics.ts" in selected
    # Only the diagnostics: not the extension, its tests or its node_modules
    assert not [
        p
        for p in selected
        if p.startswith("editors/")
        and not p.startswith("editors/vscode/src/core/")
        and p != "editors/vscode/scripts/diagnose.ts"
    ]


@pytest.mark.unit
def test_the_command_finds_the_diagnostics():
    assert command.diagnostics_driver().is_file()
