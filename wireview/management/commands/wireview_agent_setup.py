"""Install the wireview agent skill into a project's ``.claude/skills/``.

The skill ships inside the wheel, but an agent only reads what sits under the
project it is working on. This command copies the packaged files there.
"""

import shutil
import typing as t
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

SKILL_NAME = "wireview"


def skill_source() -> Path:
    """Locate the canonical skill directory.

    Installed from a wheel it sits inside the package; in a checkout of this
    repository it sits at the repository root, which is what the repository
    itself dogfoods through ``.claude/skills/wireview``.
    """
    package_root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        package_root / "agent_skills" / SKILL_NAME,
        package_root.parent / "skills" / SKILL_NAME,
    ]
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file():
            return candidate
    raise CommandError(f"the wireview agent skill is missing; looked in {[str(c) for c in candidates]}")


class Command(BaseCommand):
    help = "Copy the wireview agent skill into this project's .claude/skills/ directory."

    def add_arguments(self, parser: t.Any) -> None:
        parser.add_argument(
            "--target",
            default=None,
            help="Project directory to install into (default: settings.BASE_DIR, else the current directory).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite an existing .claude/skills/wireview directory.",
        )

    def handle(self, *args: t.Any, **options: t.Any) -> None:
        source = skill_source()
        target_root = Path(options["target"] or getattr(settings, "BASE_DIR", None) or Path.cwd()).resolve()
        destination = target_root / ".claude" / "skills" / SKILL_NAME

        if destination.is_symlink():
            # A checkout of this repository dogfoods the skill through a symlink.
            # Replacing it with a copy would silently fork the canonical files.
            self.stdout.write(self.style.WARNING(f"{destination} is a symlink; leaving it alone"))
            return

        if destination.exists():
            if not options["force"]:
                raise CommandError(f"{destination} already exists; pass --force to overwrite it")
            shutil.rmtree(destination)

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)

        files = sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file())
        self.stdout.write(self.style.SUCCESS(f"Installed the wireview agent skill into {destination}"))
        for name in files:
            self.stdout.write(f"  {name}")
        self.stdout.write("")
        self.stdout.write("Agents that do not read .claude/skills/ (Codex, for one) need a pointer instead:")
        self.stdout.write("  add a line to AGENTS.md telling them to read .claude/skills/wireview/SKILL.md")
