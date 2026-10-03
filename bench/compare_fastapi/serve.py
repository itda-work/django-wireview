"""What uvicorn runs: each application inside the same timing wrapper.

``uvicorn bench.compare_fastapi.serve:wireview`` or ``...serve:fastapi``. Each name
imports only its own stack, so a server process carries one of them.
"""

from bench.compare_fastapi.timing import Timed


def __getattr__(name: str):
    if name == "wireview":
        from bench.compare_fastapi.wv.asgi import application
    elif name == "fastapi":
        from bench.compare_fastapi.fastapi_app.main import app as application
    else:
        raise AttributeError(name)
    return Timed(application)
