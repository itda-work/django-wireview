"""What ships, and what must not (#93)."""

import json
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def test_package_json_is_private_and_says_the_same_version():
    """The npm manifest only builds the bundle. ``private`` keeps it off npm by accident,
    and a second version number that drifts from the real one (it sat at 0.2.1 through
    0.4.0) says nothing true."""
    package = json.loads((ROOT / "package.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert package["private"] is True
    assert package["version"] == project["version"]


def test_the_classifiers_name_the_supported_range():
    """docs/COMPATIBILITY.md: the Django releases Django supports, on Python 3.12 and up."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    djangos = {c.rsplit(" :: ", 1)[1] for c in project["classifiers"] if c.startswith("Framework :: Django :: ")}
    pythons = {
        c.rsplit(" :: ", 1)[1] for c in project["classifiers"] if c.startswith("Programming Language :: Python :: 3.")
    }
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert all(f'"{v}"' in ci for v in djangos | pythons), "the CI matrix and the classifiers disagree"
    assert f"django>={min(djangos, key=lambda v: tuple(map(int, v.split('.'))))}" in project["dependencies"]


WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    import yaml  # djlint and pre-commit bring it with the dev extras

    # YAML 1.1 reads the bare key `on` as True.
    return {("on" if k is True else k): v for k, v in yaml.safe_load((WORKFLOWS / name).read_text()).items()}


#: What must pass before a tag reaches PyPI (#122): the tests, the tests on the
#: dependencies a fresh install resolves (#127) and on the lowest ones pyproject.toml
#: allows (#132), quality, the package build, and the documentation site build (#160).
GATE_JOBS = {"test", "test-latest", "test-lowest", "test-e2e", "lint", "typecheck", "build", "docs-site"}


def test_publishing_waits_for_the_whole_ci_workflow():
    """Before #122 a tag went from build straight to PyPI, with no test run at all."""
    release = _workflow("release.yml")
    ci = _workflow("ci.yml")
    jobs = release["jobs"]
    gates = {name for name, job in jobs.items() if job.get("uses") == "./.github/workflows/ci.yml"}

    assert "workflow_call" in ci["on"], "release.yml cannot call ci.yml"
    assert GATE_JOBS <= set(ci["jobs"]), f"ci.yml lost {GATE_JOBS - set(ci['jobs'])}"
    assert gates, "release.yml does not run ci.yml"
    assert all("if" not in jobs[g] for g in gates), "the gate is skipped on some runs"
    needs = jobs["publish"]["needs"]
    needs = {needs} if isinstance(needs, str) else set(needs)
    assert gates <= needs, "publish does not wait for the tests"
    assert "smoke" in needs, "publish does not wait for the wheel smoke test"
    assert "make ci-smoke" in [step.get("run") for step in jobs["smoke"]["steps"]]
    assert "if" not in jobs["smoke"], "the smoke test is skipped on some runs"


def _needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _runs(job: dict) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


#: The runs a release workflow sees: a pushed tag of either workflow, a manual run from main and a
#: manual run from a tag (``gh workflow run --ref <tag>``, whose ref is the tag). A re-run keeps its
#: run's event, so it is one of these too.
RUNS = [
    (event, ref)
    for event in ("push", "workflow_dispatch")
    for ref in ("refs/heads/main", "refs/tags/v1.3.0", "refs/tags/vscode-v0.1.0")
    if not (event == "push" and ref == "refs/heads/main")
]


def _condition(expression: str, event: str, ref: str) -> bool:
    """The job conditions used here: ``&&`` of ``github.event_name == '...'`` and
    ``startsWith(github.ref, '...')``. Anything else fails, so a new form gets a reading here first."""
    result = True
    for term in (t.strip() for t in expression.split("&&")):
        if match := re.fullmatch(r"github\.event_name == '([^']+)'", term):
            result = result and event == match.group(1)
        elif match := re.fullmatch(r"startsWith\(github\.ref, '([^']+)'\)", term):
            result = result and ref.startswith(match.group(1))
        else:
            raise AssertionError(f"cannot read the condition {term!r}")
    return result


def _runs_on(workflow: str, job: str) -> set[tuple[str, str]]:
    """The runs of RUNS that start ``workflow`` (a push only on a tag its filter matches) and run ``job``."""
    filters = _workflow(workflow)["on"]["push"]["tags"]
    condition = _workflow(workflow)["jobs"][job].get("if")
    started = [
        (event, ref)
        for event, ref in RUNS
        if event == "workflow_dispatch" or _matches(filters, ref.removeprefix("refs/tags/"))
    ]
    return {run for run in started if condition is None or _condition(condition, *run)}


def _step(job: dict, uses: str) -> list[dict]:
    return [step for step in job.get("steps", []) if step.get("uses", "").split("@")[0] == uses]


def _under_dist(path: str) -> bool:
    return path.strip().lstrip("./").split("/", 1)[0] == "dist"


def test_ci_builds_the_documentation_site():
    """The release calls ci.yml whole, so this job is what keeps a broken docs build off PyPI (#160)."""
    job = _workflow("ci.yml")["jobs"]["docs-site"]

    assert "make docs-site" in _runs(job)
    assert "if" not in job


def test_the_release_packs_the_documentation_site_outside_dist():
    """pypa/gh-action-pypi-publish uploads dist/ whole: a tarball there goes to PyPI as a package file."""
    jobs = _workflow("release.yml")["jobs"]
    docs = jobs["docs"]
    uploads = {
        step["with"]["name"]: step["with"]["path"]
        for j in jobs.values()
        for step in _step(j, "actions/upload-artifact")
    }

    assert "make docs-site-bundle" in _runs(docs)
    assert "if" not in docs, "the bundle is skipped on some runs (the dry run among them)"
    assert set(uploads) == {"dist", "docs-site"}
    assert _under_dist(uploads["dist"]) and not _under_dist(uploads["docs-site"])
    assert any("wireview/VERSION" in run and "GITHUB_REF_NAME" in run for run in _runs(docs)), (
        "nothing checks the bundle names the tag"
    )
    makefile = (ROOT / "Makefile").read_text()
    assert "\ndocs-site-bundle: docs-site\n" in makefile


def test_smoke_checks_that_dist_holds_only_the_wheel_and_the_sdist():
    """PyPI gets what dist/ holds; #160's condition is that it stays the wheel and the sdist."""
    smoke = _workflow("release.yml")["jobs"]["smoke"]
    downloads = {step["with"]["name"]: step["with"]["path"] for step in _step(smoke, "actions/download-artifact")}
    listing = next(step["run"] for step in smoke["steps"] if step.get("name") == "List what would be published")

    assert {"build", "docs"} <= _needs(smoke)
    assert _under_dist(downloads["dist"]) and not _under_dist(downloads["docs-site"])
    assert "django_wireview-$version-py3-none-any.whl" in listing and "django_wireview-$version.tar.gz" in listing
    assert "find dist -mindepth 1" in listing, "the listing does not look at everything in dist/"
    assert any("tar -xzf" in run and "site-dist/" in run for run in _runs(smoke)), "the bundle is not unpacked"


def test_publishing_uploads_dist_and_attests_the_bundle():
    publish = _workflow("release.yml")["jobs"]["publish"]
    downloads = {step["with"]["name"]: step["with"]["path"] for step in _step(publish, "actions/download-artifact")}
    (pypi,) = _step(publish, "pypa/gh-action-pypi-publish")
    (attest,) = _step(publish, "actions/attest")
    (release,) = _step(publish, "softprops/action-gh-release")
    names = [step.get("uses", "").split("@")[0] for step in publish["steps"]]
    assets = release["with"]["files"].split()

    assert {"ci", "docs", "smoke"} <= _needs(publish), "a docs build that fails does not stop the publish"
    assert _runs_on("release.yml", "publish") == {("push", "refs/tags/v1.3.0")}, "a manual run publishes (#163)"
    assert _under_dist(downloads["dist"]) and not _under_dist(downloads["docs-site"])
    assert pypi["with"]["packages-dir"].rstrip("/") == "dist"
    assert "docs-site-" in attest["with"]["subject-path"] and not _under_dist(attest["with"]["subject-path"])
    assert names.index("actions/attest") < names.index("softprops/action-gh-release")
    assert "dist/*" in assets and attest["with"]["subject-path"] in assets
    assert publish["permissions"] == {
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
        "contents": "write",
    }


def test_the_build_starts_from_an_empty_dist():
    """``ci-smoke`` installs every wheel in ``dist/`` and ``ci-build`` checks the last by
    name, so a wheel an older build left there was smoked in place of the new one (it
    failed on the starter template, which older wheels do not have)."""
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\nci-build:", 1)[1].split("\n\n", 1)[0]

    assert "uv build --clear" in recipe


def test_the_development_status_says_whether_the_version_is_a_prerelease():
    """A final release says ``5 - Production/Stable`` and a pre-release does not. The
    classifier is bumped by hand in the release commit, and nothing read it: a 1.0.0 wheel
    would have gone to PyPI as ``4 - Beta``."""
    from packaging.version import Version

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    statuses = [c for c in project["classifiers"] if c.startswith("Development Status :: ")]
    stable = not Version(project["version"]).is_prerelease

    assert len(statuses) == 1, statuses
    assert (statuses[0] == "Development Status :: 5 - Production/Stable") == stable, (project["version"], statuses)


def test_the_lock_files_say_the_same_version():
    """The release commit bumps the manifests; ``uv lock`` and ``npm install --package-lock-only``
    carry the version into the locks, and the release steps did not say so."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = [p["version"] for p in lock["package"] if p["name"] == project["name"]]
    npm_lock = json.loads((ROOT / "package-lock.json").read_text())

    assert locked == [project["version"]]
    assert npm_lock["version"] == npm_lock["packages"][""]["version"] == project["version"]


def test_the_sdist_holds_the_package_and_nothing_else_of_the_repository():
    """The include patterns were not anchored, so they matched at any depth (#156).

    Every README.md, CHANGELOG.md and LICENSE in the repository went into the
    sdist: the examples' and the docs', and with editors/vscode installed, its
    node_modules'. Asked of the builder, as test_agent_skill.py does.
    """
    hatchling_sdist = pytest.importorskip("hatchling.builders.sdist")

    selected = {f.relative_path for f in hatchling_sdist.SdistBuilder(str(ROOT)).recurse_included_files()}

    tops = {path.split("/", 1)[0] for path in selected}
    assert tops <= {
        "wireview",
        "skills",
        # Only the template diagnostics the wheel ships (#179); test_check_templates.py holds the rest out
        "editors",
        "README.md",
        "hatch_build.py",
        "CHANGELOG.md",
        "LICENSE",
        "pyproject.toml",
        ".gitignore",
    }
    assert {"README.md", "CHANGELOG.md", "LICENSE", "hatch_build.py"} <= selected
    assert "wireview/templatetags/wireview.py" in selected


# The editor extension's release (#163): its own tags, its own gate, one .vsix for every registry.
LIBRARY_TAGS = ["v1.3.0", "v1.0.0rc4", "v2.0.0"]
EXTENSION_TAGS = ["vscode-v0.1.0", "vscode-v1.2.3"]
PUBLISHING_JOBS = {"marketplace", "open-vsx", "github-release"}


def _tag_filters(name: str) -> list[str]:
    return _workflow(name)["on"]["push"]["tags"]


def _matches(filters: list[str], tag: str) -> bool:
    from fnmatch import fnmatchcase  # GitHub's filters as used here: `*` and a `[0-9]` class

    return any(fnmatchcase(tag, pattern) for pattern in filters)


def test_a_tag_starts_one_release_workflow():
    """``v*`` matched ``vscode-v0.1.0`` too: an extension tag would have released the library to PyPI."""
    library, extension = _tag_filters("release.yml"), _tag_filters("vscode-release.yml")

    assert [tag for tag in LIBRARY_TAGS if not _matches(library, tag) or _matches(extension, tag)] == []
    assert [tag for tag in EXTENSION_TAGS if not _matches(extension, tag) or _matches(library, tag)] == []


def test_the_extension_release_gates_on_the_same_jobs_as_ci():
    """Copies of ci.yml's two extension jobs (calling ci.yml would run the library's whole matrix).
    The host tests are outside the library's gate and inside this one."""
    ci = _workflow("ci.yml")["jobs"]
    jobs = _workflow("vscode-release.yml")["jobs"]

    for name in ("vscode-extension", "vscode-extension-host"):
        copy = {key: value for key, value in ci[name].items() if key != "if"}
        assert jobs[name] == copy, f"{name} differs from ci.yml's"
    for name in PUBLISHING_JOBS - {"github-release"}:
        assert {"vscode-extension", "vscode-extension-host", "package"} <= _needs(jobs[name]), name
    assert {"package", "marketplace", "open-vsx"} <= _needs(jobs["github-release"])


def test_the_extension_tag_must_be_the_manifest_version_with_a_dated_changelog_section():
    package = _workflow("vscode-release.yml")["jobs"]["package"]
    check = next(step for step in package["steps"] if step.get("id") == "version")["run"]

    assert "if" not in package, "the package is skipped on some runs (the dry run among them)"
    assert '"vscode-v$version" != "$GITHUB_REF_NAME"' in check
    assert "package.json" in check
    assert r"^## \[$version\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" in check, "an undated section can be published"


def test_every_registry_gets_the_one_vsix_the_package_job_built():
    jobs = _workflow("vscode-release.yml")["jobs"]
    (upload,) = _step(jobs["package"], "actions/upload-artifact")
    packaging = " ".join(_runs(jobs["package"]))

    assert "vsce package" in packaging and "/blob/$ref/" in packaging and "/raw/$ref/" in packaging, (
        "the packaged README does not link the tag"
    )
    assert "extension/images/icon.png" in packaging and "extension/readme.md" in packaging
    for name in PUBLISHING_JOBS:
        (download,) = _step(jobs[name], "actions/download-artifact")
        assert download["with"]["name"] == upload["with"]["name"], name
        assert "vsce package" not in " ".join(_runs(jobs[name])), f"{name} builds its own package"
    assert "vsce publish --azure-credential --skip-duplicate --packagePath" in " ".join(_runs(jobs["marketplace"]))
    assert "ovsx publish --trusted-publishing --skip-duplicate --packagePath" in " ".join(_runs(jobs["open-vsx"]))


def test_a_rerun_of_a_publishing_job_passes_once_its_registry_has_the_version():
    """A registry can store the upload and the job still fail (the answer lost, the runner gone).
    Without --skip-duplicate the re-run fails on that version for good, and github-release never runs:
    vsce and ovsx both raise on a version that exists unless told to skip it."""
    jobs = _workflow("vscode-release.yml")["jobs"]

    for name, command in (("marketplace", "vsce publish"), ("open-vsx", "ovsx publish")):
        (publish,) = [run for run in _runs(jobs[name]) if command in run]
        assert "--skip-duplicate" in publish.split(), name


def test_the_extension_publishes_from_its_environment_with_least_permissions():
    """OIDC for the registries, write access to the repository only where the release is made."""
    workflow = _workflow("vscode-release.yml")
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    for name in PUBLISHING_JOBS | {"marketplace-identity"}:
        assert jobs[name]["environment"] == "vscode-marketplace", name
    writers = {name for name, job in jobs.items() if job.get("permissions", {}).get("contents") == "write"}
    assert writers == {"github-release"}
    oidc = {name for name, job in jobs.items() if job.get("permissions", {}).get("id-token") == "write"}
    assert oidc == {"marketplace", "open-vsx", "marketplace-identity"}
    for name in ("marketplace", "marketplace-identity"):
        (login,) = _step(jobs[name], "azure/login")
        assert login["with"] == {
            "client-id": "${{ vars.AZURE_CLIENT_ID }}",
            "tenant-id": "${{ vars.AZURE_TENANT_ID }}",
            "allow-no-subscriptions": True,
        }, name


def test_the_extension_release_is_never_the_latest_release():
    """The repository's latest release is the library's: the README badge and `gh release download` read it."""
    (release,) = _step(_workflow("vscode-release.yml")["jobs"]["github-release"], "softprops/action-gh-release")

    assert release["with"]["make_latest"] == "false"
    assert release["with"]["prerelease"] is False and release["with"]["draft"] is False


def test_a_dry_run_of_the_extension_release_publishes_nothing():
    """A manual run checks, packages and prints the Marketplace identity of the managed identity."""
    workflow = _workflow("vscode-release.yml")
    jobs = workflow["jobs"]

    assert "workflow_dispatch" in workflow["on"]
    # A manual run from a tag has the tag's ref (#163): only the pushed tag publishes
    pushed = {("push", "refs/tags/vscode-v0.1.0")}
    assert {name: _runs_on("vscode-release.yml", name) for name in PUBLISHING_JOBS} == dict.fromkeys(
        PUBLISHING_JOBS, pushed
    )
    assert _runs_on("vscode-release.yml", "marketplace-identity") == {
        run for run in RUNS if run[0] == "workflow_dispatch"
    }
    assert jobs["marketplace-identity"]["if"] == "github.event_name == 'workflow_dispatch'"
    identity = " ".join(_runs(jobs["marketplace-identity"]))
    assert "_apis/profile/profiles/me" in identity and "499b84ac-1321-427f-aa17-267ca6975798" in identity
    for name, job in jobs.items():
        if name in PUBLISHING_JOBS:
            continue
        runs = " ".join(_runs(job))
        assert not re.search(r"\b(vsce|ovsx) publish\b", runs), name
        assert not _step(job, "softprops/action-gh-release"), name
