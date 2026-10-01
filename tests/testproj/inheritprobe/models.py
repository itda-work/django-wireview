"""A multi-table inheritance pair for the instance ``mutation()`` receives (#153).

Django's serializer writes a model's own table only, so a ``Restaurant`` payload
carries none of ``Place``'s columns. No migrations: the test database creates
the tables, as it does for ``bookmarks``.
"""

from django.db import models


class Place(models.Model):
    name = models.CharField(max_length=50)
    city = models.CharField(max_length=50)


class Restaurant(Place):
    serves_pizza = models.BooleanField(default=False)
