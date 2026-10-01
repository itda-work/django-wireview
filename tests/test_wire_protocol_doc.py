"""Guard: the message tables of docs/implementation/wire-protocol.md name what the code sends.

The document is the canon of the message shapes, and its tables had drifted: an
outbound ``dispatch_event`` that never reaches the browser, no ``crashed`` session
mail, no ``upload.completed`` or ``session_invalidated`` fan-out. Each table's
first column is compared with the names read from the source, so adding or
removing a message without the table fails here.
"""

import ast
import re
from pathlib import Path

import pytest

from wireview.session import WireviewSession

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "implementation" / "wire-protocol.md").read_text(encoding="utf-8")
SOURCE = [ast.parse(path.read_text(encoding="utf-8")) for path in (ROOT / "wireview").rglob("*.py")]


def _table(section: str) -> set[str]:
    """The backticked names in the first column of the table under ``## <section>.``."""
    body = re.split(r"^## ", DOC, flags=re.M)
    text = next(part for part in body if part.startswith(f"{section}."))
    return set(re.findall(r"^\| `([\w.]+)` \|", text, re.M))


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _calls(name: str):
    for tree in SOURCE:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called == name:
                    yield node


def _session_methods(prefix: str) -> set[str]:
    return {name.removeprefix(prefix) for name in dir(WireviewSession) if name.startswith(prefix)}


def _outbound() -> set[str]:
    return {name for call in _calls("send_command") if call.args and (name := _literal(call.args[0]))}


def _fan_out() -> set[str]:
    found = set()
    for tree in SOURCE:
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if key is not None and _literal(key) == "type" and (name := _literal(value)):
                        found.add(name)
    for helper in ("send_to", "asend_to"):
        for call in _calls(helper):
            # utils.send_to(channel, type, ...); WireviewMeta.send_to(session, command) is session mail
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                if call.func.value.id == "self":
                    continue
            kind = (
                call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "type"), None)
            )
            if name := _literal(kind):
                found.add(name)
    found |= {name for call in _calls("_publish") if len(call.args) > 1 and (name := _literal(call.args[1]))}
    return found - {"message_from_component"}  # the envelope of session mail (section 1)


def test_inbound_is_every_command_the_session_takes():
    assert _table("2") == _session_methods("command_")


def test_outbound_is_every_command_the_session_sends():
    assert _table("3") == _outbound()


def test_session_mail_is_every_component_command_that_is_not_relayed_by_name():
    assert _table("4") == _session_methods("component_") - _outbound()


def test_fan_out_is_every_type_that_is_published():
    published = _fan_out()
    assert _table("5") == published
    for name in published:
        assert callable(getattr(WireviewSession, name.replace(".", "_"), None)), f"no handler for {name}"


def test_the_source_is_read():
    assert {"render", "error", "stream_op", "push_event"} <= _outbound()
    assert {"notification", "model_mutation", "upload.progress", "session_invalidated"} <= _fan_out()
