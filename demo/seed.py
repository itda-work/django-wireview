"""What the demo starts from, and the reset that brings it back.

The examples ship no data: poll, quiz, rating and search show an empty list until someone
fills them in the admin. ``reset()`` empties every example's tables, deletes the guests and
every session, and writes the rows below.
"""

from __future__ import annotations

import random
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction

from examples.chat import models as chat
from examples.chat_lv2 import models as chat_lv2
from examples.chat_lv3 import models as chat_lv3
from examples.chat_lv4 import models as chat_lv4
from examples.dashboard.models import Activity, Stat
from examples.notifications.models import Notification
from examples.poll.models import Option, Poll
from examples.quiz.models import Choice, Question, Quiz, Submission
from examples.rating.models import Product, Rating
from examples.search.models import Book
from examples.todo.models import Item

from .guests import PREFIX

POLLS = (
    ("Which channel layer do you run?", ("Redis", "NATS", "In-memory (single process)")),
    ("How do you write a page's interactivity today?", ("htmx", "React", "Plain Django forms", "wireview")),
)

QUIZ = (
    "wireview basics",
    "Three questions on how a component lives.",
    (
        (
            "Where does a component's state live between events?",
            "In the signed data-state of the page, and in the server's memory while the socket is open.",
            (("In the page's signed state", True), ("In a cookie", False), ("In localStorage", False)),
        ),
        (
            "What carries a render to the browser?",
            "A WebSocket. There is no HTTP fallback.",
            (("Server-sent events", False), ("A WebSocket", True), ("Long polling", False)),
        ),
        (
            "Which methods can the browser call?",
            "Methods your code defines that do not start with an underscore.",
            (
                ("Any method", False),
                ("Public methods your code defines", True),
                ("Only methods named on_*", False),
            ),
        ),
    ),
)

PRODUCTS = (
    ("Mechanical keyboard", "Tactile switches, 75% layout."),
    ("Noise-cancelling headphones", "Thirty hours on a charge."),
    ("Standing desk", "Two motors, four memory presets."),
    ("Ultrawide monitor", "34 inches, 144 Hz."),
)

BOOKS = (
    ("Two Scoops of Django", "Daniel and Audrey Feldroy", "Web", 2020),
    ("Fluent Python", "Luciano Ramalho", "Python", 2022),
    ("Programming Phoenix LiveView", "Bruce Tate, Sophie DeBenedetto", "Elixir", 2023),
    ("Designing Data-Intensive Applications", "Martin Kleppmann", "Systems", 2017),
    ("The Pragmatic Programmer", "David Thomas, Andrew Hunt", "Craft", 2019),
    ("Clean Architecture", "Robert C. Martin", "Craft", 2017),
    ("Refactoring", "Martin Fowler", "Craft", 2018),
    ("High Performance Browser Networking", "Ilya Grigorik", "Web", 2013),
    ("Architecture Patterns with Python", "Harry Percival, Bob Gregory", "Python", 2020),
    ("Test-Driven Development with Python", "Harry Percival", "Python", 2017),
    ("Effective Python", "Brett Slatkin", "Python", 2019),
    ("Python Concurrency with asyncio", "Matthew Fowler", "Python", 2022),
    ("Web Scalability for Startup Engineers", "Artur Ejsmont", "Systems", 2015),
    ("Database Internals", "Alex Petrov", "Systems", 2019),
    ("Hypermedia Systems", "Carson Gross, Adam Stepinski, Deniz Akşimşek", "Web", 2023),
    ("Real-Time Phoenix", "Stephen Bussey", "Elixir", 2020),
    ("Elixir in Action", "Saša Jurić", "Elixir", 2019),
    ("Release It!", "Michael T. Nygard", "Systems", 2018),
    ("A Philosophy of Software Design", "John Ousterhout", "Craft", 2018),
    ("Django for Professionals", "William S. Vincent", "Web", 2022),
)

STATS = (
    ("revenue", "Revenue", Decimal("48210.00"), Decimal("12.5"), "dollar", "green"),
    ("users", "Active users", Decimal("1893"), Decimal("4.2"), "users", "blue"),
    ("orders", "Orders", Decimal("312"), Decimal("-2.1"), "cart", "yellow"),
    ("conversion", "Conversion", Decimal("3.4"), Decimal("0.6"), "chart", "red"),
)

#: The stats that count something, and so stay whole numbers
COUNTS = ("users", "orders")

ACTIVITY_PEOPLE = ("Ada", "Grace", "Linus", "Margaret", "Ken", "Barbara")
ACTIVITY_LINES = {
    "login": "signed in",
    "purchase": "bought a standing desk",
    "upload": "uploaded a report",
    "comment": "commented on the roadmap",
    "update": "updated their profile",
}

TODOS = (("Open this page in a second window", False), ("Tick an item in one of them", False))


def reset() -> None:
    """Empty the examples, delete guests and sessions, and seed again."""
    with transaction.atomic():
        for model in (
            chat.Message,
            chat.Room,
            chat_lv2.Message,
            chat_lv2.Membership,
            chat_lv2.Room,
            chat_lv3.Message,
            chat_lv3.Membership,
            chat_lv3.Room,
            chat_lv4.Message,
            chat_lv4.Membership,
            chat_lv4.Room,
            Activity,
            Stat,
            Notification,
            Option,
            Poll,
            Submission,
            Choice,
            Question,
            Quiz,
            Rating,
            Product,
            Book,
            Item,
        ):
            model.objects.all().delete()
        get_user_model().objects.filter(username__startswith=PREFIX).delete()
        Session.objects.all().delete()
        seed()


def seed() -> None:
    for question, options in POLLS:
        poll = Poll.objects.create(question=question)
        Option.objects.bulk_create(Option(poll=poll, text=text, order=i) for i, text in enumerate(options))

    title, description, questions = QUIZ
    quiz = Quiz.objects.create(title=title, description=description)
    for order, (text, explanation, choices) in enumerate(questions):
        question = Question.objects.create(quiz=quiz, text=text, explanation=explanation, order=order)
        Choice.objects.bulk_create(
            Choice(question=question, text=choice, is_correct=correct, order=i)
            for i, (choice, correct) in enumerate(choices)
        )

    Product.objects.bulk_create(Product(name=name, description=description) for name, description in PRODUCTS)
    Book.objects.bulk_create(
        Book(title=title, author=author, category=category, published_year=year, description=f"A {category} book.")
        for title, author, category, year in BOOKS
    )

    for name, label, value, change, icon, color in STATS:
        Stat.objects.create(name=name, label=label, value=value, change_percent=change, icon=icon, color=color)
    for _ in range(12):
        add_activity()

    for module in (chat, chat_lv2, chat_lv3, chat_lv4):
        module.Room.objects.create(name="Lobby")
    for text, completed in TODOS:
        Item.objects.create(text=text, completed=completed)


def add_activity() -> Activity:
    kind = random.choice(tuple(ACTIVITY_LINES))
    person = random.choice(ACTIVITY_PEOPLE)
    return Activity.objects.create(type=kind, user_name=person, description=ACTIVITY_LINES[kind])


#: The dashboard's feed keeps no more than this many activities: the pulse deletes older ones
ACTIVITY_KEEP = 100


def pulse() -> None:
    """Move one stat and add an activity, so the dashboard shows something arriving.

    Saved rows reach every open dashboard through AUTO_BROADCAST, the way a real feed would.
    """
    stat = Stat.objects.order_by("?").first()
    if stat is not None:
        step = Decimal(random.uniform(-0.03, 0.04)).quantize(Decimal("0.0001"))
        # Users and orders are counts
        places = Decimal("1") if stat.name in COUNTS else Decimal("0.01")
        stat.value = max(Decimal("0"), (stat.value * (1 + step)).quantize(places))
        stat.change_percent = (stat.change_percent + step * 100).quantize(Decimal("0.01"))
        stat.change_percent = max(Decimal("-99.99"), min(Decimal("99.99"), stat.change_percent))
        stat.save()
    add_activity()
    stale = Activity.objects.order_by("-created_at").values_list("pk", flat=True)[ACTIVITY_KEEP:]
    Activity.objects.filter(pk__in=list(stale)).delete()
