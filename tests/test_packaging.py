"""What ships, and what must not (#93)."""

import json
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
    assert publish["if"] == "startsWith(github.ref, 'refs/tags/v')"
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
        "README.md",
        "hatch_build.py",
        "CHANGELOG.md",
        "LICENSE",
        "pyproject.toml",
        ".gitignore",
    }
    assert {"README.md", "CHANGELOG.md", "LICENSE", "hatch_build.py"} <= selected
    assert "wireview/templatetags/wireview.py" in selected
