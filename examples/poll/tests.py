"""What this example is for: an event that writes to the database and lets the
model broadcast drive the re-render (skip_render + mutation)."""

import pytest

from wireview import mount

from .live import XPoll
from .models import Option, Poll


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_vote_is_counted_once():
    poll = await Poll.objects.acreate(question="점심?")
    option = await Option.objects.acreate(poll=poll, text="국밥")

    view = await mount(XPoll, poll=poll)
    await view.call("vote", option_id=option.pk)

    await option.arefresh_from_db()
    assert option.votes == 1
    assert view.component.voted_option_id == option.pk


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_voting_twice_changes_nothing():
    poll = await Poll.objects.acreate(question="저녁?")
    option = await Option.objects.acreate(poll=poll, text="라면")

    view = await mount(XPoll, poll=poll)
    await view.call("vote", option_id=option.pk)
    await view.call("vote", option_id=option.pk)

    await option.arefresh_from_db()
    assert option.votes == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_choice_is_remembered_in_the_url():
    poll = await Poll.objects.acreate(question="야식?")
    option = await Option.objects.acreate(poll=poll, text="치킨")

    view = await mount(XPoll, poll=poll)
    await view.call("vote", option_id=option.pk)

    assert view.wire.params["voted"] == str(option.pk)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_option_of_another_poll_is_not_counted():
    # option_id comes from the browser
    poll = await Poll.objects.acreate(question="점심?")
    other = await Option.objects.acreate(poll=await Poll.objects.acreate(question="저녁?"), text="라면")

    view = await mount(XPoll, poll=poll)
    await view.call("vote", option_id=other.pk)

    await other.arefresh_from_db()
    assert other.votes == 0
    assert view.component.voted_option_id is None


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_address_s_choice_is_restored():
    poll = await Poll.objects.acreate(question="점심?")
    option = await Option.objects.acreate(poll=poll, text="국밥")
    view = await mount(XPoll, poll=poll, params={"voted": str(option.pk)})
    assert view.component.voted_option_id == option.pk


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("value", ["other", "abc", "-1", "0", "", "9" * 40, "x" * 5000, "999999"])
async def test_a_choice_the_address_names_that_is_not_this_poll_s_leaves_the_vote_open(value):
    """The query is the user's to type: only an option of this poll marks it voted."""
    poll = await Poll.objects.acreate(question="점심?")
    option = await Option.objects.acreate(poll=poll, text="국밥")
    other = await Option.objects.acreate(poll=await Poll.objects.acreate(question="저녁?"), text="짜장")
    view = await mount(XPoll, poll=poll, params={"voted": str(other.pk) if value == "other" else value})

    assert view.component.voted_option_id is None
    await view.call("vote", option_id=option.pk)
    await option.arefresh_from_db()
    assert option.votes == 1
