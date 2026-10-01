"""The library's string enums print as their value (found with tutorial 08).

``class X(str, Enum)`` compares equal to its value but formats as ``X.MEMBER``,
so ``<li class="{{ entry.status }}">`` rendered ``class="UploadStatus.UPLOADING"``
and a CSS rule for ``.uploading`` never matched. ``StrEnum`` prints the value.
"""

import pytest
from django.template import Context, Template

from wireview import AsyncState
from wireview.features.presence import PresenceState
from wireview.features.uploads import UploadStatus

pytestmark = pytest.mark.unit

MEMBERS = [UploadStatus.UPLOADING, AsyncState.LOADING, PresenceState.TYPING]


@pytest.mark.parametrize("member", MEMBERS, ids=lambda m: type(m).__name__)
def test_a_template_prints_the_value(member):
    assert Template("{{ member }}").render(Context({"member": member})) == member.value


@pytest.mark.parametrize("member", MEMBERS, ids=lambda m: type(m).__name__)
def test_str_and_format_are_the_value_and_equality_stays(member):
    assert str(member) == f"{member}" == member.value
    assert member == member.value
