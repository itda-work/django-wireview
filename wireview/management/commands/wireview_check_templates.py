"""Check a project's templates the way the editor extension does, and fail when they are wrong (#179).

The checks are the extension's (``editors/vscode/src/core``), run by node outside
the editor: an error is what Django or django-wireview raises when it renders the
template, said before it does. This command writes the project's metadata
(``wireview_lsp``) to a temporary file, runs the diagnostics over the templates,
prints what they found and exits 1 when any of it is an error -- with
``--strict``, a warning too. Exit 2 means the check could not run.

Usage:
    python manage.py wireview_check_templates
    python manage.py wireview_check_templates myapp/templates --strict
    python manage.py wireview_check_templates --format json
"""

from __future__ import annotations

import json
import shutil
import site
import subprocess
import sysconfig
import tempfile
import typing as t
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from wireview.management.commands.wireview_lsp import extract_metadata, template_roots

#: node runs the ``.ts`` sources as they are (type stripping without a flag) from this release.
NODE_MINIMUM = (22, 18)


def diagnostics_driver() -> Path:
    """Locate ``scripts/diagnose.ts``.

    Installed from a wheel it sits inside the package (hatch_build.py puts it
    there); in a checkout of this repository it is the extension's own.
    """
    package_root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        package_root / "template_diagnostics" / "scripts" / "diagnose.ts",
        package_root.parent / "editors" / "vscode" / "scripts" / "diagnose.ts",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise CommandError(
        f"the template diagnostics are missing; looked in {[str(c) for c in candidates]}",
        returncode=2,
    )


def node_version(node: str) -> tuple[int, int]:
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30, check=False)
    except OSError as error:
        raise CommandError(f"cannot run {node}: {error}", returncode=2) from error
    try:
        major, minor = (int(part) for part in result.stdout.strip().lstrip("v").split(".")[:2])
    except ValueError:
        raise CommandError(f"{node} --version said {result.stdout.strip()!r}, not a node version", returncode=2)
    return major, minor


def find_node(given: str | None) -> str:
    node = given or shutil.which("node")
    if not node:
        raise CommandError(
            "node is required to check templates (22.18 or later); install it or pass --node",
            returncode=2,
        )
    version = node_version(node)
    if version < NODE_MINIMUM:
        wanted = ".".join(map(str, NODE_MINIMUM))
        raise CommandError(
            f"node {'.'.join(map(str, version))} cannot run the diagnostics; {wanted} or later can",
            returncode=2,
        )
    return node


#: Directory names installers put packages in, wherever the environment is.
SITE_DIR_NAMES = {"site-packages", "dist-packages"}


def installed_package_dirs() -> list[Path]:
    """The interpreter's own site directories."""
    dirs = {sysconfig.get_paths()[key] for key in ("purelib", "platlib")}
    dirs.update(site.getsitepackages())
    if site.ENABLE_USER_SITE:
        dirs.add(site.getusersitepackages())
    return [Path(d).resolve() for d in dirs]


def is_installed(root: Path, site_dirs: list[Path]) -> bool:
    """Whether a template directory belongs to an installed package: its templates are its own to check.

    By the directory's name as well as the interpreter's site directories: an
    environment layered over another (``uv run --with``, a ``.pth`` that adds a
    directory) keeps packages in a site-packages of its own, and Django's
    templates there were checked as the project's. An editable install keeps its
    source where the project is, so its templates are still checked.
    """
    return any(root.is_relative_to(d) for d in site_dirs) or not SITE_DIR_NAMES.isdisjoint(root.parts)


def project_template_roots() -> list[Path]:
    """The template directories the project's loaders search, less those of installed packages."""
    site_dirs = installed_package_dirs()
    return [root for root in template_roots() if not is_installed(root, site_dirs)]


def shown(path: str) -> str:
    resolved = Path(path).resolve()
    cwd = Path.cwd().resolve()
    return str(resolved.relative_to(cwd)) if resolved.is_relative_to(cwd) else path


class Command(BaseCommand):
    help = "Check the project's templates for what Django or django-wireview would raise; exit 1 if anything is wrong."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "paths",
            nargs="*",
            help="Template files or directories (default: the template directories outside installed packages)",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail on warnings too (an unknown tag, an argument a component does not take)",
        )
        parser.add_argument(
            "--format",
            choices=["text", "json"],
            default="text",
            help="text: one line per problem; json: the diagnostics' own report",
        )
        parser.add_argument("--node", help="The node executable (default: node on PATH)")

    def handle(self, *args: t.Any, **options: t.Any) -> None:
        driver = diagnostics_driver()
        node = find_node(options["node"])
        targets = [Path(p) for p in options["paths"]] or project_template_roots()
        if not targets:
            raise CommandError("no template directories to check; name them", returncode=2)
        missing = [str(p) for p in targets if not p.exists()]
        if missing:
            raise CommandError(f"no such file or directory: {', '.join(missing)}", returncode=2)

        with tempfile.TemporaryDirectory() as scratch:
            metadata = Path(scratch) / "metadata.json"
            metadata.write_text(json.dumps(extract_metadata()), encoding="utf-8")
            result = subprocess.run(
                [node, str(driver), *(["--strict"] if options["strict"] else []), str(metadata), *map(str, targets)],
                capture_output=True,
                text=True,
                check=False,
            )
        try:
            # node exits 1 on an uncaught error as well as on findings: only a report says which
            report: dict[str, list[dict[str, t.Any]]] | None = json.loads(result.stdout)
        except ValueError:
            report = None
        if result.returncode not in (0, 1) or not isinstance(report, dict):
            raise CommandError(f"the diagnostics failed:\n{result.stderr.strip()}", returncode=2)
        if not report:
            # A gate pointed at the wrong directory would pass every time
            raise CommandError(f"no .html templates in {', '.join(map(str, targets))}", returncode=2)

        if options["format"] == "json":
            self.stdout.write(json.dumps(report, indent=1, ensure_ascii=False))
        else:
            self.write_text(report)
        if result.returncode:
            raise CommandError("templates have problems that fail the check", returncode=1)

    def write_text(self, report: dict[str, list[dict[str, t.Any]]]) -> None:
        counts: dict[str, int] = {}
        for path, problems in report.items():
            for p in problems:
                counts[p["severity"]] = counts.get(p["severity"], 0) + 1
                style = {"error": self.style.ERROR, "warning": self.style.WARNING}.get(p["severity"], str)
                self.stdout.write(
                    f"{shown(path)}:{p['line']}:{p['column']}: {style(p['severity'])} [{p['code']}] {p['message']}"
                )
        files = f"{len(report)} template{'s' if len(report) != 1 else ''}"
        if counts:
            found = ", ".join(
                f"{n} {severity}{'s' if n != 1 and severity != 'information' else ''}"
                for severity, n in sorted(counts.items())
            )
            self.stdout.write(f"{found} in {files}")
        else:
            self.stdout.write(self.style.SUCCESS(f"No problems in {files}"))
