"""Telemetry signals (GAP-022): opt-in, measured once, and off by default."""

import typing as t

import pytest
from django.template import Template

from wireview import Component, telemetry
from wireview.core.transport import set_broker
from wireview.repository import ComponentRepository
from wireview.testing import mount

# Rendering crosses channels' ``database_sync_to_async``, which calls
# ``close_old_connections()`` on the way in and out. Under pytest-django a test
# that opened a connection leaves it inside a transaction, so this hop hits the
# blocker and a test that never touches the ORM fails with "Database access not
# allowed" — but only when such a test ran earlier in the same process. Asking
# for the database here states what the render path already needs and takes the
# ordering out of it.
pytestmark = pytest.mark.django_db


TEMPLATE_SOURCE = "{% load wireview %}<div {% tag_header %}><p>{{ count }}</p></div>"
_template: Template | None = None


class TelemetryProbe(Component):
    _template_name = "telemetry_probe.html"

    count: int = 0

    async def increment(self, amount: int = 1):
        self.count += amount

    async def explode(self):
        raise RuntimeError("boom")

    @classmethod
    def _get_template(cls, template_name=None):
        global _template
        if _template is None:
            _template = Template(TEMPLATE_SOURCE)
        return _template


class RecordingBroker:
    """Broker double so fan-out never reaches a channel layer."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, t.Any]]] = []

    async def publish(self, topic: str, message: dict[str, t.Any]) -> None:
        self.published.append((topic, message))

    async def send_to_session(self, session_id: str, message: dict[str, t.Any]) -> None:
        return None


class Recorder:
    """Collects everything the wireview telemetry signals emit."""

    SIGNALS = (
        telemetry.event_handled,
        telemetry.component_rendered,
        telemetry.diff_computed,
        telemetry.broadcast_published,
    )

    def __init__(self) -> None:
        self.calls: list[tuple[telemetry.Signal, dict[str, t.Any]]] = []

    def connect(self) -> None:
        for signal in self.SIGNALS:
            signal.connect(self._receive, weak=False)

    def disconnect(self) -> None:
        for signal in self.SIGNALS:
            signal.disconnect(self._receive)

    def _receive(self, signal, **kwargs):
        self.calls.append((signal, kwargs))

    def of(self, signal) -> list[dict[str, t.Any]]:
        return [kwargs for sig, kwargs in self.calls if sig is signal]


@pytest.fixture
def recorder():
    """A recorder connected to every signal. Telemetry stays off."""
    rec = Recorder()
    rec.connect()
    try:
        yield rec
    finally:
        rec.disconnect()


@pytest.fixture
def telemetry_on(recorder):
    """A recorder with telemetry enabled for the duration of the test."""
    telemetry.enable()
    try:
        yield recorder
    finally:
        telemetry.disable()


@pytest.fixture
def broker():
    recording = RecordingBroker()
    set_broker(recording)
    try:
        yield recording
    finally:
        set_broker(None)


@pytest.mark.unit
def test_telemetry_is_off_by_default():
    assert telemetry.is_enabled() is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_nothing_is_emitted_while_telemetry_is_off(recorder, broker):
    from wireview.utils import asend_notification

    view = await mount(TelemetryProbe)
    view._repo.is_live = True

    view.render()
    await view.component._render_diff(view._repo)
    await asend_notification("orders", action="created")

    assert recorder.calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_render_and_diff_are_measured_separately(telemetry_on):
    view = await mount(TelemetryProbe)
    view._repo.is_live = True

    await view.component._render_diff(view._repo)

    (render,) = telemetry_on.of(telemetry.component_rendered)
    (diff,) = telemetry_on.of(telemetry.diff_computed)

    assert render["sender"] is TelemetryProbe
    assert render["component_id"] == view.component.id
    assert render["component_name"] == "TelemetryProbe"
    assert render["live"] is True
    assert render["duration_ms"] >= 0
    assert render["payload_size"] > 0
    assert render["error"] is None

    assert diff["sender"] is TelemetryProbe
    assert diff["changed"] is True
    assert diff["payload_size"] > 0
    assert diff["error"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_an_unchanged_render_reports_a_diff_that_did_not_change(telemetry_on):
    view = await mount(TelemetryProbe)
    view._repo.is_live = True

    await view.component._render_diff(view._repo)
    telemetry_on.calls.clear()
    await view.component._render_diff(view._repo)

    (diff,) = telemetry_on.of(telemetry.diff_computed)
    assert diff["changed"] is False
    assert diff["payload_size"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_http_render_is_reported_as_not_live(telemetry_on):
    view = await mount(TelemetryProbe)
    assert view._repo.is_live is False

    view.render()

    (render,) = telemetry_on.of(telemetry.component_rendered)
    assert render["live"] is False
    assert render["payload_size"] > 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_event_handling_is_measured(telemetry_on):
    view = await mount(TelemetryProbe)
    repo = ComponentRepository(is_live=True)
    repo.register_component(view.component)

    await repo.dispatch_event(view.component.id, "increment", [], {"amount": 3})

    assert view.component.count == 3
    (event,) = telemetry_on.of(telemetry.event_handled)
    assert event["sender"] is TelemetryProbe
    assert event["component_id"] == view.component.id
    assert event["event"] == "increment"
    assert event["payload_size"] == len('{"amount":3}')
    assert event["error"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_failing_handler_reports_the_error_and_still_raises(telemetry_on):
    view = await mount(TelemetryProbe)
    repo = ComponentRepository(is_live=True)
    repo.register_component(view.component)

    with pytest.raises(RuntimeError, match="boom"):
        await repo.dispatch_event(view.component.id, "explode", [], {})

    (event,) = telemetry_on.of(telemetry.event_handled)
    assert isinstance(event["error"], RuntimeError)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fan_out_through_the_process_broker_is_measured(telemetry_on, broker):
    from wireview.utils import asend_notification

    await asend_notification("orders", action="created")

    (published,) = telemetry_on.of(telemetry.broadcast_published)
    assert published["sender"] is RecordingBroker
    assert published["topic"] == "orders"
    assert published["payload_size"] == telemetry.payload_size(broker.published[0][1])
    assert published["error"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_component_broadcast_is_measured(telemetry_on):
    from wireview.core.meta import WireviewMeta

    recording = RecordingBroker()
    meta = WireviewMeta(params={}, channel_name="specific.abc", broker=recording)

    await meta.queue_broadcast("orders", action="joined")

    (published,) = telemetry_on.of(telemetry.broadcast_published)
    assert published["topic"] == "orders"
    assert published["sender"] is RecordingBroker
    assert recording.published[0][0] == "orders"


@pytest.mark.unit
def test_payload_size_measures_wire_bytes_and_gives_up_gracefully():
    assert telemetry.payload_size(None) is None
    assert telemetry.payload_size("héllo") == len("héllo".encode())
    assert telemetry.payload_size(b"1234") == 4
    assert telemetry.payload_size({"a": 1}) == len('{"a":1}')

    class Unserializable:
        def __repr__(self):
            raise TypeError("nope")

    assert telemetry.payload_size({"a": Unserializable()}) is None


@pytest.mark.unit
def test_a_disabled_span_measures_nothing():
    assert telemetry.is_enabled() is False
    with telemetry.span(telemetry.diff_computed, sender=None) as span:
        assert span.enabled is False
        span.measure("x" * 1000)
        span.annotate(changed=True)

    telemetry.enable()
    try:
        with telemetry.span(telemetry.diff_computed, sender=None) as span:
            assert span.enabled is True
    finally:
        telemetry.disable()
