"""The code in docs/DEPLOYMENT.md runs (#124).

The deployment guide used to declare Prometheus metrics with nothing wired to
them, and a receiver whose argument names drift from a signal's fails only in
the project that copied it. These take the blocks out of the document and run
them: the receivers against stand-ins for ``prometheus_client`` and
``opentelemetry``, the readiness view against real channel layers.
"""

import json
import re
import sys
import types
import typing as t
from pathlib import Path

import pytest
from channels.layers import InMemoryChannelLayer
from django.test import RequestFactory

from wireview import telemetry

ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT = ROOT / "docs" / "DEPLOYMENT.md"


def block(first_line: str) -> str:
    """The ```python block of DEPLOYMENT.md whose first line is ``first_line``."""
    for code in re.findall(r"```python\n(.*?)```", DEPLOYMENT.read_text(), re.S):
        if code.startswith(first_line):
            return code
    raise AssertionError(f"no python block in DEPLOYMENT.md starts with {first_line!r}")


class Metric:
    """Records every call made on it or on its labelled children."""

    def __init__(self, log: list, name: str, labels: dict | None = None) -> None:
        self._log = log
        self._name = name
        self._labels = labels or {}

    def labels(self, **labels: t.Any) -> "Metric":
        return Metric(self._log, self._name, labels)

    def __getattr__(self, method: str):
        def record(*args: t.Any, **kwargs: t.Any) -> None:
            self._log.append((self._name, method, self._labels, args))

        return record


@pytest.fixture
def calls(monkeypatch):
    """Stand-ins for the metrics libraries; every metric call lands in the list."""
    log: list = []

    def factory(name, *args, **kwargs):
        return Metric(log, name)

    prometheus = types.ModuleType("prometheus_client")
    prometheus.Counter = prometheus.Gauge = prometheus.Histogram = factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "prometheus_client", prometheus)

    meter = types.SimpleNamespace(create_up_down_counter=factory, create_counter=factory, create_histogram=factory)
    otel = types.ModuleType("opentelemetry")
    otel.metrics = types.SimpleNamespace(get_meter=lambda name: meter)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opentelemetry", otel)
    return log


@pytest.fixture
def receivers():
    """Runs a block's receivers for the test, and disconnects them after."""
    namespaces: list[dict] = []

    def load(first_line: str) -> dict:
        namespace: dict = {}
        exec(compile(block(first_line), str(DEPLOYMENT), "exec"), namespace)
        namespaces.append(namespace)
        return namespace

    yield load
    signals = [
        getattr(telemetry, name) for name in telemetry.__all__ if isinstance(getattr(telemetry, name), telemetry.Signal)
    ]
    for namespace in namespaces:
        for value in namespace.values():
            if callable(value):
                for signal in signals:
                    signal.disconnect(value)


def fire_everything() -> None:
    """Each signal once, with exactly the arguments wireview sends."""
    sender = object
    telemetry.connection_opened.send(sender=sender, connection_id="c1")
    telemetry.connection_closed.send(sender=sender, connection_id="c1", code=1012, duration_ms=1500.0, components=2)
    telemetry.connection_closed.send(sender=sender, connection_id="c0", code=None, duration_ms=None, components=0)
    telemetry.join_rejected.send(sender=sender, reason="expired", component_name="Counter", detail="expired")
    telemetry.publish_failed.send(
        sender=sender, kind="send_to_session", target="specific.x!y", error=RuntimeError(), dropped=True
    )
    telemetry.event_handled.send(
        sender=sender,
        component_id="c",
        component_name="Counter",
        event="bump",
        duration_ms=2.0,
        payload_size=10,
        error=ValueError(),
    )
    telemetry.component_rendered.send(
        sender=sender,
        component_id="c",
        component_name="Counter",
        live=True,
        duration_ms=4.0,
        payload_size=100,
        error=None,
    )


@pytest.mark.unit
def test_the_prometheus_receivers_take_what_the_signals_send(calls, receivers):
    receivers("# myapp/metrics.py")
    fire_everything()

    assert ("wireview_connections", "inc", {}, ()) in calls
    # Only the socket that was counted in is counted out
    assert [c for c in calls if c[:2] == ("wireview_connections", "dec")] == [("wireview_connections", "dec", {}, ())]
    assert ("wireview_connection_closes_total", "inc", {"code": "1012"}, ()) in calls
    assert ("wireview_connection_seconds", "observe", {}, (1.5,)) in calls
    assert ("wireview_join_rejected_total", "inc", {"reason": "expired"}, ()) in calls
    assert (
        "wireview_publish_failed_total",
        "inc",
        {"kind": "send_to_session", "dropped": "true"},
        (),
    ) in calls
    assert ("wireview_event_errors_total", "inc", {"component": "Counter"}, ()) in calls
    assert ("wireview_render_seconds", "observe", {"component": "Counter"}, (0.004,)) in calls


@pytest.mark.unit
def test_the_opentelemetry_receivers_take_what_the_signals_send(calls, receivers):
    receivers("# myapp/otel_metrics.py")
    fire_everything()

    assert [c[3] for c in calls if c[0] == "wireview.connections"] == [(1,), (-1,)]
    assert ("wireview.join.rejected", "add", {}, (1, {"reason": "expired"})) in calls
    assert ("wireview.publish.failed", "add", {}, (1, {"kind": "send_to_session", "dropped": True})) in calls
    assert ("wireview.render.duration", "record", {}, (4.0, {"component": "Counter"})) in calls


# --- readiness -------------------------------------------------------------------


class UnreachableLayer(InMemoryChannelLayer):
    async def group_send(self, group, message):
        raise ConnectionError("broker unreachable")


class SilentLayer(InMemoryChannelLayer):
    """Accepts a message and never delivers it: a broker that lost the subscription."""

    async def group_send(self, group, message):
        return None


async def ask(monkeypatch, layer) -> tuple[int, dict]:
    namespace: dict = {}
    exec(compile(block("# myapp/health.py"), str(DEPLOYMENT), "exec"), namespace)
    monkeypatch.setitem(namespace, "get_channel_layer", lambda: layer)
    namespace["ROUND_TRIP_TIMEOUT"] = 0.2
    response = await namespace["channel_layer_ready"](RequestFactory().get("/ready/"))
    return response.status_code, json.loads(response.content)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_readiness_passes_when_the_layer_delivers(monkeypatch):
    layer = InMemoryChannelLayer()
    status, body = await ask(monkeypatch, layer)

    assert (status, body) == (200, {"status": "ready"})
    # The group it made is gone
    assert layer.groups == {}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("layer", [None, UnreachableLayer(), SilentLayer()], ids=["none", "unreachable", "silent"])
async def test_readiness_fails_when_the_layer_does_not(monkeypatch, layer):
    status, body = await ask(monkeypatch, layer)

    assert status == 503
    assert body["status"] == "unready"


def blocks(language: str) -> list[str]:
    """Every ```<language> block of DEPLOYMENT.md."""
    return re.findall(rf"```{language}\n(.*?)```", DEPLOYMENT.read_text(), re.S)


@pytest.mark.parametrize("language", ["nginx", "caddyfile"])
def test_every_proxy_that_sends_the_app_requests_serves_static_files(language):
    # In production the starter's ASGIStaticFilesHandler is off. A proxy that sends /static/ on to
    # the app gets a 404 for wireview.min.js: the page draws and no component ever joins.
    proxies = [code for code in blocks(language) if "proxy_pass" in code or "reverse_proxy" in code]
    assert proxies
    for code in proxies:
        assert "/static/" in code, f"this {language} block sends /static/ to the app:\n{code}"


def test_the_docker_image_collects_static_files():
    # Without collectstatic the image has nothing for the front server to serve at /static/.
    images = blocks("dockerfile")
    assert images
    for code in images:
        assert "collectstatic" in code, code
