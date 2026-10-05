"""What uvicorn runs: each application inside the same timing wrapper.

``uvicorn bench.compare_fastapi.serve:wireview`` or ``...serve:fastapi``. Each name
imports only its own stack, so a server process carries one of them.
"""

import dataclasses
import os

from bench.compare_fastapi.timing import Timed


def __getattr__(name: str):
    if name == "wireview":
        from bench.compare_fastapi.wv.asgi import application

        if os.environ.get("BENCH_SHARED_RENDER") == "1":
            _share_the_board()
    elif name == "fastapi":
        from bench.compare_fastapi.fastapi_app.main import app as application
    else:
        raise AttributeError(name)
    return Timed(application)


def _share_the_board() -> None:
    """``BENCH_SHARED_RENDER=1``: the board as an application that declares ``Meta.shared_render`` (#176).

    That is one line in ``Board``'s ``class Meta`` (``shared_render = True``). It is put on
    here rather than written in live.py, so the implementation the README measures and
    counts stays the one without it; docs/design/broadcast-fanout.md §7 counts the line.
    """
    from bench.compare_fastapi.wv.board.live import Board

    Board._meta = dataclasses.replace(Board._meta, shared_render=True)
