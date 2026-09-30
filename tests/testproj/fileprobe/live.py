"""A component that uploads every way the library offers, in a real browser.

tests/test_uploads_e2e.py drives it: the file input one file after another past
``max_entries=1``, the drop zone, the upload button, the image preview, and an
external upload whose "presigned URL" is a view of this fixture
(``views.put_target``). The page links to itself and to a page without the
component, so a test can end the instance while its uploads are in the air.
``FileParent`` hides and shows a LiveComponent that uploads, under the same id:
each showing is a new instance with its own config.
"""

from django.urls import reverse

from wireview import Component, ExternalUploadMeta, LiveComponent


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
