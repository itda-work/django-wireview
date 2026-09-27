"""A component that uploads every way the library offers, in a real browser.

tests/test_uploads_e2e.py drives it: the file input one file after another past
``max_entries=1``, the drop zone, the image preview, and an external upload
whose "presigned URL" is a view of this fixture (``views.put_target``).
"""

from django.urls import reverse

from wireview import Component, ExternalUploadMeta


class FileProbe(Component):
    class Meta:
        template_name = "fileprobe/probe.html"

    received: list[str] = []
    external_done: str = ""

    async def joined(self):
        self.allow_upload("files", accept=[".txt"], max_entries=1)
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
