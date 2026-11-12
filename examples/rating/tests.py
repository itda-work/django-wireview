"""What this example is for: transient UI state (hover) next to persisted state
(the rating), and URL parameters that survive a reload."""

import re

import pytest
from asgiref.sync import sync_to_async

from wireview import mount

from .live import XRatingStats, XStarRating
from .models import Product, Rating


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_rating_saves_once_per_session():
    product = await Product.objects.acreate(name="키보드")
    view = await mount(XStarRating, product=product, session_key="s1")

    await view.call("rate", score=4)
    await view.call("rate", score=2)

    assert view.component.current_rating == 2
    assert await Rating.objects.filter(product=product, session_key="s1").acount() == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_score_outside_one_to_five_is_ignored():
    product = await Product.objects.acreate(name="마우스")
    view = await mount(XStarRating, product=product, session_key="s2")

    await view.call("rate", score=9)

    assert view.component.current_rating == 0
    assert not await Rating.objects.filter(product=product, session_key="s2").aexists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_hover_is_preview_only():
    product = await Product.objects.acreate(name="모니터")
    view = await mount(XStarRating, product=product, session_key="s3")

    await view.call("set_hover", star=5)
    assert view.component.hover_rating == 5
    assert not await Rating.objects.filter(product=product, session_key="s3").aexists()

    await view.call("clear_hover")
    assert view.component.hover_rating == 0


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_readonly_widget_does_not_save():
    product = await Product.objects.acreate(name="스피커")
    view = await mount(XStarRating, product=product, session_key="s4", readonly=True)

    await view.call("rate", score=5)

    assert not await Rating.objects.filter(product=product, session_key="s4").aexists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_each_bar_of_the_distribution_is_its_share_of_the_ratings():
    """It read ``count|divisibleby:total``, a yes or no.

    Every bar was ``width: True%`` or ``False%``, so none showed.
    """
    product = await Product.objects.acreate(name="헤드폰")
    for i, score in enumerate((5, 5, 5, 3)):
        await Rating.objects.acreate(product=product, score=score, session_key=f"d{i}")
    view = await mount(XRatingStats, product=product)

    # render() reads the ratings through the ORM: sync code, so off the event loop
    html = await sync_to_async(view.render)()
    bars = re.findall(r'class="distribution-bar"\s+style="width: ([^"]*)"', html)

    # Five stars first: 3 of 4, then none at four, 1 of 4 at three, none at two and one
    assert bars == ["75%", "0%", "25%", "0%", "0%"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_address_s_score_shows_when_it_is_one_to_five():
    product = await Product.objects.acreate(name="키보드")
    view = await mount(XStarRating, product=product, session_key="s5", params={"rating": "4"})
    assert view.component.current_rating == 4


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("value", ["7", "-1", "0", "abc", "", "9" * 40, "x" * 5000])
async def test_a_score_the_address_names_outside_one_to_five_is_the_saved_one(value):
    """The query is the user's to type: a score no one can give does not cover the saved one."""
    product = await Product.objects.acreate(name="키보드")
    await Rating.objects.acreate(product=product, session_key="s6", score=2)
    view = await mount(XStarRating, product=product, session_key="s6", params={"rating": value})
    assert view.component.current_rating == 2
