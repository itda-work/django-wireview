import sys
import typing as t
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# StrEnum is available in Python 3.11+, use (str, Enum) for 3.10 compatibility
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:

    class StrEnum(str, Enum):
        """String enum for Python 3.10 compatibility."""

        pass


class AutoBroadcast(BaseModel):
    # pydantic before 2.10 reserves every model_ name and warned about model_pk at import.
    model_config = ConfigDict(protected_namespaces=())

    # model-a
    model: bool = False
    # model-a.1234
    model_pk: bool = False
    # model-b.9876.model-a-set
    related: bool = False
    # model-b.9876.model-a-set and model-a.1234.model-b-set, whichever side changed
    m2m: bool = False
    # The models to broadcast, as ('app_label', 'ModelName') pairs. A set sends every
    # field of each; a mapping says per model which fields go onto the channel layer:
    # "__all__", a tuple of field names, or () for the pk alone (#144).
    senders: set[tuple[str, str]] | dict[tuple[str, str], t.Literal["__all__"] | tuple[str, ...]] = Field(
        default_factory=set
    )

    def _fields_for(self, sender: tuple[str, str]) -> tuple[str, ...] | None:
        """The field names ``sender``'s payload carries as written, or None for every field."""
        fields = self.senders.get(sender, "__all__") if isinstance(self.senders, dict) else "__all__"
        return None if fields == "__all__" else fields


class ModelAction(StrEnum):
    # Model action
    UPDATED = "UPDATED"
    DELETED = "DELETED"
    CREATED = "CREATED"

    # M2M actions
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    CLEARED = "CLEARED"
