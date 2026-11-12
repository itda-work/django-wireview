"""Every way into a session that runs a component's code owes the same things (#232, #233).

``docs/design/session-entry-gate.md`` lists the duties a way in carries by hand --
the rest live in the sinks it goes through (``send_render``, ``render_diff``,
``register_component``, the adapter's ``dispatch``, ``stream()``). This file
states them as a table and checks every row the same way, as
``tests/test_live_session_contract.py`` does for the page boundary.

**The table is read against the code.** Channels hands a message to the
session's method named after its type, so every public coroutine of
``WireviewSession`` is a way in -- ``command_*`` from the page, ``component_*``
mail from its components, the rest from the channel layer or the adapter.
A new one without a row fails ``test_every_way_in_has_a_row``.

**A duty is probed, and so is its control.** Each probe is paired with the same
call on a healthy component, which has to do something visible: a probe that
passes because the way in does nothing proves nothing.

The duties:

- ``recovers`` -- component code that raises costs that component alone: an
  ``error`` for it (its root, for a LiveComponent), the socket stays, a
  component beside it stays (#94, ``docs/features/errors.md``).
- ``chores`` -- what the code changed about the component's subscriptions is
  heard from then on (``after_mutation_chores``).
- ``instance`` -- mail names its component with the instance that sent it, and
  mail from one a join has since replaced under the id leaves the new one be
  (#137, #207, #233).
- ``refuses`` -- what a failed join left runs nothing (``repo.reachable``).
"""

from __future__ import annotations

import ast
import inspect
import typing as t
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from testproj.outbound import RecordingOutbound

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.features.uploads import UploadEntry, UploadStatus
from wireview.repository import ComponentRepository
from wireview.session import WireviewSession, _Held

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

ROOT = Path(__file__).resolve().parent.parent / "wireview"

#: The channel a probe hears notifications on, and the one its code tunes it to
HEARS = "gate-hears"
TUNED = "gate-tuned"

#: The ids whose template was read: a render ran
READS: list[str] = []


# --- the probes ---------------------------------------------------------------------------------


class _Acts:
    """What a probe's code does when asked: ``ok`` counts, ``explode`` makes its next
    render raise, ``tune`` moves it to another channel, ``break`` makes its
    ``get_subscriptions()`` raise."""

    explode: bool
    count: int
    topic: str
    bad_topics: bool
    hears: bool
    upload_mode: str

    def _act(self, mode: str) -> None:
        if mode == "ok":
            self.count += 1
        elif mode == "explode":
            self.explode = True
        elif mode == "tune":
            self.topic = TUNED
        elif mode == "break":
            self.bad_topics = True

    @property
    def boom(self) -> str:
        READS.append(self.id)  # type: ignore[attr-defined]
        if self.explode:
            raise RuntimeError("render went wrong")
        return "ok"

    def get_subscriptions(self) -> set[str]:
        if self.bad_topics:
            raise RuntimeError("subscriptions went wrong")
        return {topic for topic in (HEARS if self.hears else "", self.topic) if topic}

    async def act(self, mode: str = "ok", **_rest: t.Any) -> None:
        self._act(mode)

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict[str, t.Any]) -> t.Any:
        self._act(event)

    async def params_changed(self, params: dict[str, t.Any], uri: str) -> None:
        # Every component hears the params: only the probe's target acts on them
        if params.get("target") == self.id:
            self._act(params.get("mode", "ok"))

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self._act(kwargs.get("mode", "ok"))

    async def on_upload_complete(self, name: str, entry: t.Any) -> None:
        self._act(self.upload_mode)

    async def update(self, **assigns: t.Any) -> None:
        self._act(assigns.get("mode", "ok"))


class GateProbe(_Acts, Component):
    explode: bool = False
    count: int = 0
    topic: str = ""
    bad_topics: bool = False
    hears: bool = False
    upload_mode: str = "ok"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% tag_header %}>{{ this.boom }} {{ count }}</p>")


class GateLeaf(_Acts, LiveComponent):
    explode: bool = False
    count: int = 0
    topic: str = ""
    bad_topics: bool = False
    hears: bool = False
    upload_mode: str = "ok"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% live_tag_header %}>{{ this.boom }} {{ count }}</p>")


@pytest.fixture(autouse=True)
def _reset_reads():
    READS.clear()
    yield
    READS.clear()


def _session() -> tuple[WireviewConsumer, RecordingOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), vsn=PROTOCOL_VERSION)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.page_uri = None
    consumer.channel_name = "test-channel"
    outbound = RecordingOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def _joined(session: WireviewConsumer, id: str, cls: str = "GateProbe", **fields: t.Any) -> t.Any:
    component = session.repo.build(cls, {"id": id, **fields})
    component.wire.has_joined = True
    return component


def _leaf(session: WireviewConsumer, parent: Component) -> GateLeaf:
    leaf = t.cast(GateLeaf, _joined(session, "gate-leaf", "GateLeaf"))
    leaf._parent_id = parent.id
    leaf.wire.parent_instance = parent.wire.instance
    parent.wire.live_instances[leaf.id] = leaf.wire.instance
    return leaf


# --- how each way in is called --------------------------------------------------------------------


def _with_instance(method: t.Callable[..., t.Any], instance: int, **kwargs: t.Any) -> t.Any:
    return method(instance=instance, **kwargs)


def _entry(component: Component, status: UploadStatus = UploadStatus.PENDING) -> UploadEntry:
    if getattr(component, "_upload_registry", None) is None:
        component.allow_upload("files", max_entries=4)
    entry = UploadEntry(ref="0", upload_name="files", client_name="a.txt", client_size=10, client_type="text/plain")
    component._upload_registry.add_entry("files", entry)  # type: ignore[union-attr]
    entry.status = status
    return entry


@dataclass
class Call:
    """How a probe reaches a way in. ``mode`` is what the component's code does there;
    ``instance`` the one the mail names (the target's own unless the probe says)."""

    session: WireviewConsumer
    target: Component
    mode: str
    instance: int


async def _user_event(c: Call) -> None:
    await c.session.command_user_event(c.target.id, "act", {}, {"mode": c.mode}, ref=7)


async def _hook_event(c: Call) -> None:
    await c.session.command_hook_event(c.target.id, "hook-1", c.mode, {}, ref="r1")


async def _navigated(c: Call) -> None:
    await c.session.command_navigated({"mode": c.mode, "target": c.target.id}, f"/gate/?mode={c.mode}", [c.target.id])


async def _params_changed(c: Call) -> None:
    await c.session.command_params_changed({"mode": c.mode, "target": c.target.id}, f"/gate/?mode={c.mode}")


def _preset(c: Call) -> None:
    """A way in that runs only the render: the render is what raises."""
    if c.mode == "explode":
        t.cast(GateProbe, c.target).explode = True


async def _upload_register(c: Call) -> None:
    _preset(c)
    if getattr(c.target, "_upload_registry", None) is None:
        c.target.allow_upload("files", max_entries=4)
    entry = {"ref": "1", "name": "b.txt", "size": 1, "type": "text/plain"}
    await c.session.command_upload_register(c.target.id, "files", [entry])


async def _upload_cancel(c: Call) -> None:
    _preset(c)
    _entry(c.target)
    await c.session.command_upload_cancel(c.target.id, "files", "0")


async def _upload_complete(c: Call) -> None:
    if c.mode == "explode":
        # The answer to an upload whose bytes are not there: the render outside on_upload_complete
        _preset(c)
        _entry(c.target)
    else:
        t.cast(GateProbe, c.target).upload_mode = c.mode
        _entry(c.target, UploadStatus.COMPLETED)
    await c.session.command_upload_complete(c.target.id, "files", "0")


async def _upload_progress(c: Call) -> None:
    _preset(c)
    _entry(c.target)
    await c.session.upload_progress({"component": c.target.id, "upload": "files", "ref": "0", "progress": 50})


async def _upload_error(c: Call) -> None:
    _preset(c)
    _entry(c.target)
    await c.session.upload_error({"component": c.target.id, "upload": "files", "ref": "0", "errors": ["no"]})


async def _dispatch_event(c: Call) -> None:
    session = c.session
    await _with_instance(
        session.component_dispatch_event,
        c.instance,
        id=c.target.id,
        command="act",
        args=[],
        kwargs={"mode": c.mode},
    )


async def _send_render(c: Call) -> None:
    _preset(c)
    await _with_instance(c.session.component_send_render, c.instance, id=c.target.id)


async def _crashed(c: Call) -> None:
    await _with_instance(c.session.component_crashed, c.instance, id=c.target.id)


async def _update_live_component(c: Call) -> None:
    leaf = t.cast(GateLeaf, c.target)
    await _with_instance(
        c.session.component_update_live_component,
        c.instance,
        parent_id=leaf._parent_id,
        live_component_id=leaf.id,
        assigns={"mode": c.mode},
    )


async def _notification(c: Call) -> None:
    t.cast(GateProbe, c.target).hears = True
    await c.session.notification({"channel": HEARS, "kwargs": {"mode": c.mode}})


async def _deliver_unheard(c: Call) -> None:
    t.cast(GateProbe, c.target).hears = True
    c.target.wire.unheard = [(0, "notification", HEARS, {"mode": c.mode})]
    await c.session.component_deliver_unheard(c.target.id, c.instance)


async def _upload_op(c: Call) -> None:
    await c.session.component_upload_op("config", "files", owner=c.target.id, instance=c.instance)


async def _release_patches(c: Call) -> None:
    held = c.session._patch_held[id(c.target)] = _Held(c.target)
    held.tokens.add(5)
    await c.session.component_release_patches(c.target.id, c.instance, 5)


# --- the table ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    #: ``inbound`` (the page's ``command_*``), ``mail`` (``component_*``), ``channel``
    #: (a channel-layer message type), ``adapter`` (the adapter or host calls it)
    kind: str
    duties: frozenset[str] = frozenset()
    call: t.Callable[[Call], t.Awaitable[None]] | None = None
    #: Why a row runs none of a component's code, or what covers it elsewhere
    why: str = ""
    #: ``"LiveComponent"`` when the way in reaches one
    target: str = "Component"


def _row(kind: str, duties: str, call: t.Callable[[Call], t.Awaitable[None]], **kwargs: t.Any) -> Row:
    return Row(kind, frozenset(duties.split()), call, **kwargs)


RELAY = "relays what a component sent to the page; runs none of its code"

TABLE: dict[str, Row] = {
    # From the page
    "command_join": Row(
        "inbound",
        why="its own recovery (_join_failed, no retry): tests/test_errors.py, tests/test_live_session_contract.py",
    ),
    "command_sync": Row("inbound", why="answers synced; runs no component code"),
    "command_leave": Row(
        "inbound", why="leaving() alone, which _call_leaving logs and swallows (tests/test_errors.py)"
    ),
    "command_navigated": _row("inbound", "recovers chores refuses", _navigated),
    "command_params_changed": _row("inbound", "recovers chores refuses", _params_changed),
    "command_user_event": _row("inbound", "recovers chores refuses", _user_event),
    "command_hook_event": _row("inbound", "recovers chores refuses", _hook_event),
    # No chores: the external callback presigns a URL and the cancel is the framework's
    "command_upload_register": _row("inbound", "recovers refuses", _upload_register),
    "command_upload_cancel": _row("inbound", "recovers refuses", _upload_cancel),
    "command_upload_complete": _row("inbound", "recovers chores refuses", _upload_complete),
    # From the components: mail
    "component_dispatch_event": _row("mail", "recovers chores instance refuses", _dispatch_event),
    "component_send_render": _row("mail", "recovers instance refuses", _send_render),
    "component_crashed": _row("mail", "instance", _crashed),
    "component_update_live_component": _row(
        "mail", "recovers chores instance refuses", _update_live_component, target="LiveComponent"
    ),
    "component_deliver_unheard": _row("mail", "recovers chores instance refuses", _deliver_unheard),
    "component_upload_op": _row("mail", "instance", _upload_op, why=RELAY),
    "component_release_patches": _row("mail", "instance", _release_patches, why="lets held frames go; no code"),
    "component_stream_op": Row("mail", why=f"{RELAY}; its hold token is the instance's own (_discard_token)"),
    "component_template_changed": Row("mail", why="the session's own mail (#180): writes rejoin"),
    "component_joined": Row("mail", why=RELAY),
    "component_remove": Row("mail", why=RELAY),
    "component_scroll_into_view": Row("mail", why=RELAY),
    "component_focus_on": Row("mail", why=RELAY),
    "component_url_change": Row("mail", why=RELAY),
    "component_title": Row("mail", why=RELAY),
    "component_flash": Row("mail", why=RELAY),
    "component_clear_flash": Row("mail", why=RELAY),
    "component_exec_js": Row("mail", why=RELAY),
    "component_push_event": Row("mail", why=RELAY),
    # From the channel layer
    "notification": _row("channel", "recovers chores refuses", _notification),
    "model_mutation": Row(
        "channel", why="notifications.deliver, as notification: test_model_mutation_is_delivered_as_a_notification"
    ),
    "upload_progress": _row("channel", "recovers refuses", _upload_progress),
    "upload_error": _row("channel", "recovers refuses", _upload_error),
    "upload_completed": Row("channel", why="state only: promotes the entry, renders nothing (#83)"),
    "session_invalidated": Row("channel", why="closes the socket; no component code"),
    "wireview_patch": Row(
        "channel",
        why="writes Broadcast frames, runs no code (#178); its refusal: test_live_session_contract.py broadcast_patch",
    ),
    # Called by the adapter or a host, not by a message of their own
    "start": Row("adapter", why="the connection begins"),
    "stop": Row("adapter", why="the connection ends: leaving() under LEAVING_SECONDS (tests/test_session_end.py)"),
    "stop_handing_over": Row("adapter", why="part of stop()"),
    "close": Row("adapter", why="closes the socket"),
    "refuse_inbound": Row(
        "adapter",
        why="closes the socket for what the page sent (#230): the consumer's websocket_receive before the frame is "
        "parsed, and command_join before it builds the instance; logs, telemetry and close, no component's code",
    ),
    "handle_message": Row("adapter", why="routes an inbound frame to its command_* row"),
    "message_from_component": Row("adapter", why="routes a mail to its component_* row"),
    "templates_changed": Row("adapter", why="the autoreloader's call; mails template_changed"),
    "send_command": Row("adapter", why="a sink: writes one outbound command"),
    "send_render": Row("adapter", why="a sink: refused, shared_render and LiveComponent lifecycles are here"),
    "after_mutation_chores": Row("adapter", why="a sink: the chores duty"),
    "update_to_which_channels_im_subscribed_to": Row("adapter", why="part of after_mutation_chores"),
    "send_query_string": Row("adapter", why="part of after_mutation_chores"),
}

#: Cells known missing, until #233 decides them. strict: one that passes fails the run.
MISSING: dict[tuple[str, str], str] = {
    # The policy question of docs/design/session-entry-gate.md §5-4: kept open (#233)
    **{
        (name, "chores raises"): "#233: get_subscriptions() raising waits for the owner's decision"
        for name, row in TABLE.items()
        if "chores" in row.duties
    },
}


def _cells(duty: str, *, control: bool = False) -> list[t.Any]:
    """The rows that owe ``duty``; a missing cell is a strict xfail, but not in a control."""
    params = []
    for name, row in TABLE.items():
        if duty.split()[0] not in row.duties:
            continue
        missing = not control and (name, duty) in MISSING
        marks = [pytest.mark.xfail(strict=True, reason=MISSING[(name, duty)])] if missing else []
        params.append(pytest.param(name, id=name, marks=marks))
    return params


def _target(session: WireviewConsumer, row: Row) -> Component:
    if row.target == "LiveComponent":
        return _leaf(session, _joined(session, "gate-root"))
    return _joined(session, "gate-1")


def _errors(outbound: RecordingOutbound) -> list[dict[str, t.Any]]:
    return [payload for command, payload in outbound.commands if command == "error"]


def _rendered(outbound: RecordingOutbound, id: str) -> bool:
    return any(command == "render" and payload["id"] == id for command, payload in outbound.commands) or any(
        command == "render" and id in payload.get("children", {}) for command, payload in outbound.commands
    )


# --- the table is the code's --------------------------------------------------------------------


def _ways_in() -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(WireviewSession)
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
    }


async def test_every_way_in_has_a_row():
    # A new public coroutine of the session is a new way in: Channels dispatches a
    # message of that type to it. Give it a row -- its duties, or why it has none.
    assert _ways_in() == set(TABLE)


async def test_the_kind_of_a_row_is_its_name():
    for name, row in TABLE.items():
        if name.startswith("command_"):
            assert row.kind == "inbound", name
        elif name.startswith("component_"):
            assert row.kind == "mail", name
        else:
            assert row.kind in ("channel", "adapter"), name


async def test_a_row_with_no_duty_says_why_and_a_duty_has_a_probe():
    for name, row in TABLE.items():
        assert row.duties or row.why, f"{name}: no duty and no reason"
        assert not row.duties or row.call is not None, f"{name}: duties and nothing to probe them with"


def _mail_sends() -> list[tuple[str, ast.Call, Path]]:
    """Every ``send("<command>", ...)`` with a literal command in the package."""
    found = []
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)) or node.func.attr != "send":
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.append((node.args[0].value, node, path))
    return found


def _instance_mails() -> list[t.Any]:
    params = []
    for name, row in TABLE.items():
        if "instance" not in row.duties:
            continue
        marks = (
            [pytest.mark.xfail(strict=True, reason=MISSING[(name, "instance")])]
            if (name, "instance") in MISSING
            else []
        )
        params.append(pytest.param(name.removeprefix("component_"), id=name, marks=marks))
    return params


@pytest.mark.parametrize("command", _instance_mails())
async def test_the_mail_is_sent_with_its_instance(command):
    sends = [(node, path) for name, node, path in _mail_sends() if name == command]
    assert sends, f'nothing sends "{command}"'
    for node, path in sends:
        keywords = {keyword.arg for keyword in node.keywords}
        assert "instance" in keywords, f'{path.name}:{node.lineno} sends "{command}" without its instance'


async def test_a_live_components_mail_names_the_parent_that_drew_it():
    # send_to_parent() mails dispatch_event under the parent's id: with the parent's instance
    session, _ = _session()
    parent = _joined(session, "gate-root")
    leaf = session.repo.build_live_component("GateLeaf", {"id": "gate-leaf"}, parent.id)
    assert leaf.wire.parent_instance == parent.wire.instance


async def test_a_parents_mail_names_the_live_component_it_drew():
    # send_update() mails update_live_component: with the instance the parent drew under the id
    session, _ = _session()
    parent = _joined(session, "gate-root")
    first = session.repo.build_live_component("GateLeaf", {"id": "gate-leaf"}, parent.id)
    assert parent.wire.live_instances == {"gate-leaf": first.wire.instance}
    # Hidden, then drawn anew under the id: the parent's record follows
    session.repo.remove(first.id)
    again = session.repo.build_live_component("GateLeaf", {"id": "gate-leaf"}, parent.id)
    assert parent.wire.live_instances == {"gate-leaf": again.wire.instance} != {"gate-leaf": first.wire.instance}


async def test_model_mutation_is_delivered_as_a_notification(monkeypatch):
    from wireview.core import notifications

    heard = []

    async def deliver(host, receiver, data):
        heard.append(receiver)

    monkeypatch.setattr(notifications, "deliver", deliver)
    session, _ = _session()
    await session.model_mutation({})
    await session.notification({})
    assert heard == ["mutation", "notification"]


# --- the duties ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", _cells("recovers"))
async def test_code_that_raises_costs_only_its_component(name):
    row = TABLE[name]
    session, outbound = _session()
    beside = _joined(session, "gate-beside")
    target = _target(session, row)
    root = session.repo.root_of(target)

    assert row.call is not None
    await row.call(Call(session, target, "explode", target.wire.instance))

    assert [error["id"] for error in _errors(outbound)] == [root.id]
    assert outbound.closed == []
    assert session.repo.get(beside.id) is beside


@pytest.mark.parametrize("name", _cells("recovers", control=True))
async def test_control_the_way_in_renders_a_healthy_component(name):
    row = TABLE[name]
    session, outbound = _session()
    target = _target(session, row)

    assert row.call is not None
    await row.call(Call(session, target, "ok", target.wire.instance))

    assert _errors(outbound) == []
    assert _rendered(outbound, target.id), f"{name} rendered nothing for a healthy component"


@pytest.mark.parametrize("name", _cells("chores"))
async def test_a_channel_the_code_moved_to_is_heard(name):
    row = TABLE[name]
    session, outbound = _session()
    target = _target(session, row)

    assert row.call is not None
    await row.call(Call(session, target, "tune", target.wire.instance))

    assert TUNED in outbound.topics


@pytest.mark.parametrize("name", _cells("chores raises"))
async def test_subscriptions_that_raise_cost_only_their_component(name):
    row = TABLE[name]
    session, outbound = _session()
    beside = _joined(session, "gate-beside")
    target = _target(session, row)

    assert row.call is not None
    await row.call(Call(session, target, "break", target.wire.instance))

    assert [error["id"] for error in _errors(outbound)] == [session.repo.root_of(target).id]
    assert session.repo.get(beside.id) is beside


def _replace(session: WireviewConsumer, row: Row, old: Component) -> Component:
    """Another join under the same id: the page's new DOM, a boosted navigation."""
    if row.target == "LiveComponent":
        session.repo.retire("gate-root")
        return _target(session, row)
    session.repo.retire(old.id)
    return _joined(session, old.id)


@pytest.mark.parametrize("name", _cells("instance"))
async def test_mail_from_a_replaced_instance_leaves_the_new_one_be(name):
    row = TABLE[name]
    session, outbound = _session()
    old = _target(session, row)
    new = _replace(session, row, old)
    assert new is not old and new.wire.instance != old.wire.instance
    outbound.commands.clear()
    READS.clear()

    assert row.call is not None
    await row.call(Call(session, new, "ok", old.wire.instance))

    assert session.repo.get(new.id) is new
    assert t.cast(GateProbe, new).count == 0
    assert READS == []
    assert outbound.commands == []
    if name == "component_release_patches":
        assert id(new) in session._patch_held


@pytest.mark.parametrize("name", _cells("instance", control=True))
async def test_control_mail_from_the_instance_reaches_it(name):
    row = TABLE[name]
    session, outbound = _session()
    target = _target(session, row)

    assert row.call is not None
    await row.call(Call(session, target, "ok", target.wire.instance))

    reached = (
        t.cast(GateProbe, target).count == 1
        or READS
        or _errors(outbound)
        or any(command == "upload_op" for command, _ in outbound.commands)
        or id(target) not in session._patch_held
    )
    assert reached, f"{name} did nothing for its own instance"


@pytest.mark.parametrize("name", _cells("refuses"))
async def test_what_a_failed_join_left_runs_nothing(name):
    row = TABLE[name]
    session, outbound = _session()
    target = _target(session, row)
    session.repo.join_failed(session.repo.root_of(target).id, [])

    assert row.call is not None
    await row.call(Call(session, target, "ok", target.wire.instance))

    assert t.cast(GateProbe, target).count == 0
    assert READS == []
    assert _errors(outbound) == []
    assert not any(command == "render" and payload.get("diff") for command, payload in outbound.commands)
