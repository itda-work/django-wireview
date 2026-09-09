"""A component whose only job is to prove an upload arrived.

The multi-worker test (tests/test_multiworker_uploads.py) opens its WebSocket on
one worker and POSTs the chunks to another. What settles whether that worked is
not the HTTP status: it is whether the worker holding the connection can read the
bytes the other one wrote. So this component reads them and puts the length and a
digest into its state, where a render diff makes them visible.
"""

import hashlib

from wireview import Component


class UploadProbe(Component):
    _template_name = "uploadprobe/probe.html"

    received: int = 0
    digest: str = ""

    async def joined(self):
        self.allow_upload("files", accept=[".txt"], max_file_size=1024 * 1024)

    async def on_upload_complete(self, name: str, entry) -> None:
        async for upload in self.consume_uploads(name):
            data = upload.read()
            self.received = len(data)
            self.digest = hashlib.sha256(data).hexdigest()[:16]
