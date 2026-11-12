"""A component that uploads every way the library offers, in a real browser.

tests/test_uploads_e2e.py drives it: the file input one file after another past
``max_entries=1``, the drop zone, the upload button, the image preview, and an
external upload whose "presigned URL" is a view of this fixture
(``views.put_target``). The page links to itself and to a page without the
component, so a test can end the instance while its uploads are in the air.
``FileParent`` hides and shows a LiveComponent that uploads, under the same id:
each showing is a new instance with its own config. ``FileShelf`` draws an
ordinary component that uploads, holding a LiveComponent that uploads, only
from an event: a live render brings both in. ``FileLateShelf`` draws the same
box only once the work its ``joined()`` starts has landed, which waits for
``FileLateShelf.gate``: a test holds it closed across a reconnect.
``FileForm`` and the ``FileFormChild`` in it take ``auto_upload=False`` files,
which go up when their form is submitted (#264).
"""

import asyncio
import threading
import typing as t

from django.urls import reverse

from wireview import AsyncResult, Component, ExternalUploadMeta, LiveComponent


class FileProbe(Component):
    class Meta:
        template_name = "fileprobe/probe.html"

    received: list[str] = []
    external_done: str = ""
    # Set by the page's ?manual=1: the same component with "files" not uploaded
    # until asked, so a test can tell which instance's config the page acted on
    manual: str = ""

    async def joined(self):
        self.allow_upload("files", accept=[".txt"], max_entries=1, auto_upload=not self.manual)
        self.allow_upload("images", accept=[".png"], max_entries=1, auto_upload=False)
        self.allow_upload("outside", accept=[".txt"], external=self._presign)

    def _presign(self, entry, component) -> ExternalUploadMeta:
        return ExternalUploadMeta(uploader="Plain", url=reverse("fileprobe:put", args=[entry.ref]))

    async def on_upload_complete(self, name: str, entry) -> None:
        if name == "outside":
            self.external_done = entry.client_name
            return
        async for upload in self.consume_uploads(name):
            self.received = [*self.received, f"{upload.name}:{len(upload.read())}"]


class FileChild(LiveComponent):
    class Meta:
        template_name = "fileprobe/child.html"

    received: list[str] = []

    async def joined(self):
        self.allow_upload("files", accept=[".txt"], max_entries=1)

    async def on_upload_complete(self, name: str, entry) -> None:
        async for upload in self.consume_uploads(name):
            self.received = [*self.received, f"{upload.name}:{len(upload.read())}"]


class FileParent(Component):
    class Meta:
        template_name = "fileprobe/parent.html"

    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown


class FileShelf(Component):
    """Draws a FileBox only once an event says so: a live render brings it in.

    The box is an ordinary ``{% component %}``, so the page joins it when it sees
    it; its ``joined()`` sets up its uploads, and the LiveComponent inside it is
    settled by the box's own render then. ``hide`` and ``show`` again build both
    anew, which start from their defaults whatever a reconnect restored before.
    """

    class Meta:
        template_name = "fileprobe/shelf.html"

    shown: bool = False

    async def show(self):
        self.shown = True

    async def hide(self):
        self.shown = False


class FileBox(Component):
    class Meta:
        template_name = "fileprobe/box.html"

    received: list[str] = []

    async def joined(self):
        self.allow_upload("files", accept=[".txt"], max_entries=1)

    async def on_upload_complete(self, name: str, entry) -> None:
        async for upload in self.consume_uploads(name):
            self.received = [*self.received, f"{upload.name}:{len(upload.read())}"]


class FileSprig(LiveComponent):
    class Meta:
        template_name = "fileprobe/sprig.html"

    joins: int = 0
    received: list[str] = []

    async def joined(self):
        self.joins += 1
        self.allow_upload("files", accept=[".txt"], max_entries=1)

    async def on_upload_complete(self, name: str, entry) -> None:
        async for upload in self.consume_uploads(name):
            self.received = [*self.received, f"{upload.name}:{len(upload.read())}"]


class FileLateShelf(Component):
    """Draws a FileBox inside what the work its ``joined()`` starts brings.

    The page's HTML has the box; the join's render, the work loading again, has
    not. On a reconnect the page joins the shelf and then the box, whose element
    it still has, before it patches that render in and lets the box go.
    """

    class Meta:
        template_name = "fileprobe/late_shelf.html"

    data: AsyncResult[str] = AsyncResult.success("ok")
    gate: t.ClassVar[threading.Event] = threading.Event()

    async def joined(self):
        self.data = await self.assign_async(self._load())

    async def _load(self) -> str:
        while not FileLateShelf.gate.is_set():
            await asyncio.sleep(0.01)
        return "ok"


class FileForm(Component):
    """A form whose uploads wait for its submit: ``auto_upload=False`` (#264).

    ``photos`` and ``notes`` are two chunked inputs of the form, ``photos`` with a
    drop zone; ``remote`` goes to the external target. The ``save`` handler takes
    what came up; ``saves`` counts how many submits reached it. ``stray`` is a
    manual input outside any form: nothing can start it, and the page says so.
    """

    class Meta:
        template_name = "fileprobe/form.html"

    title: str = ""
    saved: list[str] = []
    saves: int = 0

    async def joined(self):
        self.allow_upload("photos", accept=[".txt", ".png"], max_entries=2, auto_upload=False)
        self.allow_upload("notes", accept=[".txt"], max_entries=1, auto_upload=False)
        self.allow_upload("remote", accept=[".txt"], external=self._presign, auto_upload=False)
        self.allow_upload("stray", accept=[".txt"], max_entries=2, auto_upload=False)

    def _presign(self, entry, component) -> ExternalUploadMeta:
        return ExternalUploadMeta(uploader="Plain", url=reverse("fileprobe:put", args=[entry.ref]))

    async def save(self, title: str = ""):
        self.saves += 1
        saved = [f"title:{title}"]
        for name in ("photos", "notes"):
            async for upload in self.consume_uploads(name):
                saved.append(f"{name}:{upload.name}:{len(upload.read())}")
        saved.extend(f"remote:{entry.client_name}" for entry in self.uploads["remote"] if entry.status == "completed")
        self.saved = saved


class FileFormChild(LiveComponent):
    """The same form inside a LiveComponent: its uploads are its own."""

    class Meta:
        template_name = "fileprobe/form_child.html"

    saved: list[str] = []

    async def joined(self):
        self.allow_upload("files", accept=[".txt"], max_entries=1, auto_upload=False)

    async def save(self):
        saved = []
        async for upload in self.consume_uploads("files"):
            saved.append(f"{upload.name}:{len(upload.read())}")
        self.saved = saved
