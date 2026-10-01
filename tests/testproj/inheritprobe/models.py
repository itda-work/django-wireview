"""Multi-table inheritance for the instance ``mutation()`` receives (#153).

Django's serializer writes a model's own table only, so a ``Restaurant`` payload
carries none of ``Place``'s columns. A child's pk is not always its parent's: the
chains below link through pks of their own and keys of another type. No migrations: the test database creates
the tables, as it does for ``bookmarks``.
"""

from django.db import models


class Place(models.Model):
    name = models.CharField(max_length=50)
    city = models.CharField(max_length=50)


class Restaurant(Place):
    serves_pizza = models.BooleanField(default=False)


class Pizzeria(Restaurant):
    """Three tables, each linked by the pk Django gives it: one pk value all the way up."""

    oven = models.CharField(max_length=20, default="")


class CustomRestaurant(Place):
    """A child with a pk of its own: the link to ``Place`` is another column, with another value."""

    restaurant_key = models.IntegerField(primary_key=True)
    serves_pizza = models.BooleanField(default=False)


class Branch(CustomRestaurant):
    """Its pk links to ``CustomRestaurant``, whose link to ``Place`` the payload does not carry."""

    open_late = models.BooleanField(default=False)


class Venue(models.Model):
    code = models.CharField(max_length=10, primary_key=True)
    name = models.CharField(max_length=50)


class Theatre(Venue):
    """A pk of another type than the parent's: an int pk, a str link."""

    number = models.IntegerField(primary_key=True)
    seats = models.IntegerField(default=0)
