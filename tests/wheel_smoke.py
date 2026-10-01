"""What a fresh ``pip install`` of the built wheel gets: does it import and start? (#122)

Run by ``make ci-smoke`` against ``dist/*.whl`` in an isolated environment that
resolves the dependencies anew, as a user's install does. 1.0.0rc3 went out with
tests green on uv.lock's pydantic while the newest pydantic, the one every new
install got, could not import the package (#127). This is the last look before
publishing, at the artifact itself rather than the source tree.

Not a pytest module: it must not see the repository's ``wireview``.
"""

import sys
from pathlib import Path

import django
from django.conf import settings

settings.configure(
    DEBUG=False,
    SECRET_KEY="wheel-smoke",
    ALLOWED_HOSTS=["localhost"],
    INSTALLED_APPS=[
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.staticfiles",
        "channels",
        "wireview",
    ],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
        }
    ],
    STATIC_URL="/static/",
)
django.setup()

import wireview  # noqa: E402

repo = Path(__file__).resolve().parent.parent
installed = Path(wireview.__file__).resolve()
if installed.is_relative_to(repo):
    sys.exit(f"imported the source tree ({installed}), not the wheel")

missing = [name for name in wireview.__all__ if getattr(wireview, name, None) is None]
if missing:
    sys.exit(f"public names that do not load: {missing}")

from django.contrib.staticfiles import finders  # noqa: E402
from django.core.management import call_command  # noqa: E402

if not finders.find("wireview/wireview.min.js"):
    sys.exit("the wheel has no wireview.min.js")

# Errors (not warnings) make check raise, and a component class is what runs
# handler validation at import -- the step that broke on pydantic 2.13.
call_command("check")


class XSmoke(wireview.Component):
    class Meta:
        template_name = "smoke.html"

    count: int = 0

    async def increment(self, by: int = 1):
        self.count += by


# The starter template renders from the installed package (#131). Only rendered:
# the project it makes runs on daphne, which the wheel does not depend on, and
# tests/test_project_template.py runs it.
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as target:
    call_command("startproject", "smokesite", target, template=str(installed.parent / "project_template"))
    made = Path(target)
    if "smokesite.settings" not in (made / "manage.py").read_text():
        sys.exit("the starter template did not render manage.py")
    expected = [
        made / "smokesite" / "asgi.py",
        made / "hello" / "templates" / "hello" / "index.html",
        made / ".gitignore",
    ]
    if not all(path.is_file() for path in expected):
        sys.exit(f"the starter template made {sorted(str(p.relative_to(made)) for p in made.rglob('*'))}")

import pydantic  # noqa: E402

print(f"wheel smoke: {installed.parent} on Django {django.get_version()}, pydantic {pydantic.VERSION}")
