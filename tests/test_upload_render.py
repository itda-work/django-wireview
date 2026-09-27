"""An upload's progress and errors reach the template; an external callback may be async (#110).

The upload guide renders ``{{ entry.progress }}`` and ``entry.errors``, but the
consumer only forwarded the numbers to the browser and never rendered, so the
template showed 0 until the upload finished. The external upload callback was
called and never awaited, so an ``async def`` presign failed.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component, ExternalUploadMeta
from wireview.consumer import WireviewConsumer
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "ur/page.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% for entry in this.uploads.files %}<b>{{ entry.progress }}%</b>"
        "{% for error in entry.errors %}<i>{{ error }}</i>{% endfor %}{% endfor %}</div>"
    )
}


class UrPage(Component):
    class Meta:
        template_name = "ur/page.html"

    async def joined(self):
        self.allow_upload("files", accept=[".txt"])
        self.allow_upload("outside", accept=[".txt"], external=self._presign)

    async def _presign(self, entry, component) -> ExternalUploadMeta:
        return ExternalUploadMeta(uploader="S3", url=f"https://bucket.example/{entry.ref}")


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


async def joined_page() -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), channel_name="c")
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "c"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    component = await consumer.repo.join("UrPage", {"id": "p"})
    await consumer.send_render(component)
    await component.wire.flush_pending()
    return consumer, outbound


async def register(consumer: WireviewConsumer, name: str) -> None:
    await consumer.command_upload_register(
        id="p", name=name, entries=[{"ref": "r1", "name": "a.txt", "size": 100, "type": "text/plain"}]
    )


def last_render(outbound: FakeOutbound) -> str:
    return str([payload for command, payload in outbound.commands if command == "render"][-1])


async def test_progress_is_rendered():
    consumer, outbound = await joined_page()
    await register(consumer, "files")

    await consumer.upload_progress({"component": "p", "upload": "files", "ref": "r1", "progress": 42})

    assert "42" in last_render(outbound)


async def test_an_error_is_rendered():
    consumer, outbound = await joined_page()
    await register(consumer, "files")

    await consumer.upload_error({"component": "p", "upload": "files", "ref": "r1", "errors": ["too big"]})

    assert "too big" in last_render(outbound)


async def test_an_async_external_callback_is_awaited():
    consumer, outbound = await joined_page()

    await register(consumer, "outside")

    ops = [payload for command, payload in outbound.commands if command == "upload_op"]
    registered = [op for op in ops if op["op"] == "registered"]
    assert registered and registered[0]["external"]["url"] == "https://bucket.example/r1", ops
