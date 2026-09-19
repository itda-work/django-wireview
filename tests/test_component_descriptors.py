"""User classmethods and staticmethods survive handler validation (found while fixing #89).

``Component.__init_subclass__`` wraps every public method in ``validate_call``.
It used to wrap what ``getattr(cls, name)`` returned and store that back as a
plain function, which dropped the descriptor: a classmethod stayed bound to the
class that defined it, so a subclass building itself through an overridden
``new`` got an instance of the parent, and a staticmethod reached through an
instance received that instance as its first argument.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from pydantic import ValidationError

from wireview import Component
from wireview.core.meta import WireviewMeta

pytestmark = pytest.mark.unit


class Parent(Component, public=False):
    label: str = ""

    @classmethod
    def new(cls, **kwargs):
        return super().new(label=cls.__name__, **kwargs)

    @classmethod
    def kind(cls) -> str:
        return cls.__name__

    @staticmethod
    def double(value: int) -> int:
        return value * 2


class Child(Parent, public=False):
    pass


def _instance(cls: type[Component]) -> Component:
    return cls(user=AnonymousUser(), wire=WireviewMeta(params={}), id="d")


def test_a_classmethod_binds_the_class_it_is_called_on():
    assert Child.kind() == "Child"
    assert Parent.kind() == "Parent"


def test_an_overridden_new_builds_the_subclass():
    built = Child.new(user=AnonymousUser(), wire=WireviewMeta(params={}), id="c")

    assert type(built) is Child
    assert built.label == "Child"


def test_a_staticmethod_works_through_an_instance():
    assert _instance(Parent).double(2) == 4
    assert Parent.double(2) == 4


def test_both_are_still_validated():
    with pytest.raises(ValidationError):
        Parent.double("not a number")
