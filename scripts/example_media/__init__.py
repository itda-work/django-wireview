"""Each example's final screen and a short clip of it working, made by a script (#213).

``make example-media`` (``python -m scripts.example_media``) seeds a database, starts the
E2E server (tests/testproj/e2e_server.py), plays every example's scene in Chromium and
writes ``examples/<name>/media/<name>.png`` and ``<name>.webp``. The scenes are data
(scenes.py) so the demo site (#214) can replay them as a smoke check without recording.
What may be committed and how large it may be is budget.py, which the tests read too.
"""
