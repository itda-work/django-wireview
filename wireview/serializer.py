import json
import typing as t

from django.core.serializers import deserialize, serialize
from django.core.serializers.base import DeserializedObject
from django.core.serializers.json import DjangoJSONEncoder
from django.db import router
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
    (data,) = json.loads(instance)
    obj: DeserializedObject = next(deserialize("python", [data]))
    return _restore(obj.object, data.get("fields", {}).keys())


def _restore(instance: Model, sent: t.Iterable[str]) -> Model:
    """Make a deserialized ``instance`` the row it was sent from, holding only the fields ``sent``.

    A field the payload did not carry is deferred, as ``QuerySet.only()`` leaves it:
    reading it queries the row (and fails on the event loop), and ``save()`` writes
    only the fields that were loaded. ``Model(**data)`` would give it the field's
    default instead, read without a word and written back over the row. Django's
    serializer writes a model's own table, so the parents of multi-table
    inheritance always arrive this way, as do ``serialize=False`` fields.

    The payload is a row that existed when it was sent: a fresh ``Model(**data)`` is
    "adding", and Django then inserts outright when the pk field has a default.
    ``save()`` narrows itself to the loaded fields only when it saves to
    ``_state.db``, so that is the alias it would write to.
    """
    sent = set(sent)
    meta = instance._meta
    for field in meta.concrete_fields:
        if field.attname != meta.pk.attname and field.name not in sent:
            instance.__dict__.pop(field.attname, None)
    _link_parents(instance, meta)
    instance._state.adding = False
    instance._state.db = router.db_for_write(type(instance), instance=instance)
    return instance


def _link_parents(instance: Model, meta: t.Any) -> None:
    """Give each parent table of ``meta`` the key its parent link holds, as far as the payload tells.

    A child's pk is not its parents' pk: a child may declare a pk of its own, and the
    link to its parent is then another column with another value, of another type
    if the parent's key is. The link is all that names the parent row. A link the
    payload did not carry (a grandparent's, past a parent with a pk of its own) stays
    deferred, so ``save()`` reads it from the row instead of guessing it.
    """
    for parent, link in meta.parents.items():
        if link is not None and link.attname in instance.__dict__:
            instance.__dict__[parent._meta.pk.attname] = instance.__dict__[link.attname]
            _link_parents(instance, parent._meta)


class WireviewJSONEncoder(DjangoJSONEncoder):
    def default(self, o: t.Any) -> t.Any:
        if isinstance(o, BaseModel):
            return o.model_dump()
        else:
            return super().default(o)
