"""What this example is for: reusable layout components whose content the caller
supplies — a card, a list, a modal, an alert — declared with `_slots`.

The library-level behaviour of `{% fill %}` and `{% render_slot %}` is covered by
tests/test_slots.py and tests/test_slots_integration.py, which render these very
components. What is checked here is the contract this example teaches: which
slots each component declares, and which of them a caller must fill.
"""

import pytest
from wireview import mount

from .components import Alert, Card, List, Modal


@pytest.mark.unit
def test_every_component_declares_the_slots_it_renders():
    assert set(Card._slots) == {"header", "footer"}
    assert set(List._slots) == {"item"}
    assert set(Modal._slots) == {"title", "body", "actions"}
    assert set(Alert._slots) == {"icon"}


@pytest.mark.unit
def test_only_the_modal_title_is_required():
    required = {
        name for component in (Card, List, Modal, Alert) for name, spec in component._slots.items() if spec["required"]
    }
    assert required == {"title"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_card_renders_without_any_fill():
    view = await mount(Card, title="제목")
    html = view.render()

    assert html is not None
    assert "제목" in html
