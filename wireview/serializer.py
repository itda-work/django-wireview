import typing as t

from django.core.serializers import deserialize, serialize
from django.core.serializers.base import DeserializedObject
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Model
from pydantic import BaseModel

__all__ = ("encode", "decode")


def encode(instance: Model) -> str:
    return serialize("json", [instance], cls=WireviewJSONEncoder)


def decode(instance: str) -> Model:
    """The model instance ``encode`` wrote, as an existing row that saves like any other.

    ``DeserializedObject.save`` is a fixture load: ``save_base(raw=True)``, which skips
    the model's ``save()``, sends its signals with ``raw=True`` and sets the m2m
    fields from the payload. A receiver that saves what it heard means a save, so
    the plain object is returned and its own ``save()`` stays (#153).
    """
    obj: DeserializedObject = list(deserialize("json", instance))[0]
    restored = obj.object
    # The payload is a row that existed when it was sent. A fresh Model(**data) is
    # "adding", and Django then inserts outright when the pk field has a default.
    restored._state.adding = False
    return restored


class WireviewJSONEncoder(DjangoJSONEncoder):
    def default(self, o: t.Any) -> t.Any:
        if isinstance(o, BaseModel):
            return o.model_dump()
        else:
            return super().default(o)
