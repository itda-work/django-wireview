"""What this example is for: reusable layout components whose content the caller
supplies — a card, a list, a modal, an alert — declared with `Meta.slots`.

The library-level behaviour of `{% fill %}` and `{% render_slot %}` is covered by
tests/test_slots.py and tests/test_slots_integration.py, which render these very
components. What is checked here is the contract this example teaches: which
slots each component declares, and which of them a caller must fill.
"""

import pytest

from wireview import mount

from .components import Alert, Card, List, Modal, SlotsGallery


@pytest.mark.unit
def test_every_component_declares_the_slots_it_renders():
    assert set(Card._meta.slots) == {"header", "footer"}
    assert set(List._meta.slots) == {"item"}
    assert set(Modal._meta.slots) == {"title", "body", "actions"}
    assert set(Alert._meta.slots) == {"icon"}


@pytest.mark.unit
def test_only_the_modal_title_is_required():
    required = {
        name
        for component in (Card, List, Modal, Alert)
        for name, spec in component._meta.slots.items()
        if spec["required"]
    }
    assert required == {"title"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_card_renders_without_any_fill():
    view = await mount(Card, title="제목")
    html = view.render()

    assert html is not None
    assert "제목" in html


@pytest.mark.integration
@pytest.mark.django_db
def test_the_gallery_page_fills_every_component(client):
    """The page the example's media is recorded from: each component drawn, each with what the page put in it."""
    html = client.get("/slots/").content.decode()

    assert 'data-name="SlotsGallery"' in html
    for name in ("Alert", "Card", "List", "Modal"):
        assert f'data-name="{name}"' in html, name
    assert "읽을 책 2권" in html
    assert "Fluent Python" in html
    assert "모달의 제목은 필수 슬롯이다" in html


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_gallery_changes_what_it_passes():
    view = await mount(SlotsGallery)

    await view.call("add_book")
    await view.call("cycle_alert")
    await view.call("toggle_modal")

    html = view.render()
    assert "읽을 책 3권" in html
    assert "alert-success" in html
    assert "modal is-open" in html
