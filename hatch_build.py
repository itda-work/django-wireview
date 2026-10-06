"""What the package says about the repository, with its links pinned to this release.

The README links into the repository with absolute ``main`` URLs, because PyPI has no
repository to resolve a relative link in. PyPI keeps a page for every release, though, and
pinned to ``main`` the 1.0.0 page would show the documents of whatever came after it, and a
moved file would break the links of every older page. The release workflow builds from the
tag ``v<version>`` (it checks the two agree), so that tag is what the links name. The project
URLs in the sidebar of the same page (``[tool.hatch.metadata.hooks.custom.urls]``) are pinned
the same way, and so is the agent skill the wheel ships: ``wireview_agent_setup`` installs it
into a project that runs this release, so its links describe this release's API.
"""

import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.metadata.plugin.interface import MetadataHookInterface

BRANCH_URLS = (
    "https://github.com/itda-work/django-wireview/blob/main/",
    "https://github.com/itda-work/django-wireview/tree/main/",
    "https://raw.githubusercontent.com/itda-work/django-wireview/main/",
)


def pin(text: str, tag: str) -> str:
    for url in BRANCH_URLS:
        text = text.replace(url, url.removesuffix("main/") + f"{tag}/")
    return text


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict) -> None:
        tag = f"v{metadata['version']}"
        readme = (Path(self.root) / "README.md").read_text(encoding="utf-8")
        metadata["readme"] = {"content-type": "text/markdown", "text": pin(readme, tag)}
        metadata["urls"] = {name: pin(url, tag) for name, url in self.config["urls"].items()}


class CustomBuildHook(BuildHookInterface):
    """Ship skills/wireview as wireview/agent_skills/wireview, its links pinned, and the template diagnostics."""

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel" or version == "editable":
            # An editable install finds the skill in the checkout (wireview_agent_setup looks there too)
            return
        tag = f"v{self.metadata.version}"
        source = Path(self.root) / "skills" / "wireview"
        if not (source / "SKILL.md").is_file():
            # The sdist has to carry it (see the sdist force-include in pyproject.toml)
            raise FileNotFoundError(f"{source / 'SKILL.md'}: the wheel would ship without the agent skill")
        self._pinned = tempfile.TemporaryDirectory()
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            target = Path(self._pinned.name) / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".md":
                target.write_text(pin(path.read_text(encoding="utf-8"), tag), encoding="utf-8")
            else:
                target.write_bytes(path.read_bytes())
        build_data["force_include"][self._pinned.name] = "wireview/agent_skills/wireview"
        self._diagnostics = tempfile.TemporaryDirectory()
        ship_template_diagnostics(Path(self.root), Path(self._diagnostics.name))
        build_data["force_include"][self._diagnostics.name] = DIAGNOSTICS_PACKAGE_PATH

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        for name in ("_pinned", "_diagnostics"):
            if hasattr(self, name):
                getattr(self, name).cleanup()


#: Where the wheel puts the template diagnostics; wireview_check_templates looks there.
DIAGNOSTICS_PACKAGE_PATH = "wireview/template_diagnostics"


def ship_template_diagnostics(root: Path, target: Path) -> None:
    """The editor extension's diagnostics as node runs them, without the extension (#179).

    ``manage.py wireview_check_templates`` runs ``scripts/diagnose.ts``, which imports
    ``src/core`` and nothing outside node, so the layout is kept and nothing else
    comes along. The package.json says the ``.ts`` files are ES modules: without it
    node decides by the nearest package.json above, which in an installed project may
    be anyone's.
    """
    source = root / "editors" / "vscode"
    driver = source / "scripts" / "diagnose.ts"
    core = sorted((source / "src" / "core").glob("*.ts"))
    if not driver.is_file() or not core:
        # The sdist has to carry them (see the sdist include in pyproject.toml)
        raise FileNotFoundError(f"{driver}: the wheel would ship without the template diagnostics")
    (target / "scripts").mkdir(parents=True)
    (target / "src" / "core").mkdir(parents=True)
    (target / "scripts" / "diagnose.ts").write_bytes(driver.read_bytes())
    for path in core:
        (target / "src" / "core" / path.name).write_bytes(path.read_bytes())
    (target / "package.json").write_text('{"private": true, "type": "module"}\n', encoding="utf-8")
