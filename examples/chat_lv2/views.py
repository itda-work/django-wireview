import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .access import is_member
from .live import end_membership
from .live_sessions import chat_lv2
from .models import Membership, Room

#: The people the demo signs in as. Open a window per person: windows that share
#: cookies share a user.
DEMO_USERS = ("alice", "bob", "carol")


def index(request: HttpRequest) -> HttpResponse:
    """The room list, and the demo sign-in for a visitor who is not signed in."""
    rooms = []
    if request.user.is_authenticated:
        mine = set(Membership.objects.filter(user=request.user).values_list("room_id", flat=True))
        rooms = [(room, room.id in mine) for room in Room.objects.all()]
    return render(
        request,
        "chat_lv2/index.html",
        {"rooms": rooms, "demo_users": DEMO_USERS, "next": request.GET.get("next", "")},
    )


@chat_lv2.view
def room(request: HttpRequest, room_id: uuid.UUID) -> HttpResponse:
    """A chat room. Signed-in members only.

    The live_session has let a signed-in user through by now. Which room this
    is, it cannot know, so the membership is asked here -- before anything is
    rendered -- and again by the boundary's hook for every component the page
    mounts.
    """
    room = get_object_or_404(Room, id=room_id)
    if not is_member(request.user, room):
        messages.info(request, f"Join {room.name} to read and write there.")
        return redirect("chat_lv2:index")
    return render(request, "chat_lv2/room.html", {"room": room})


@require_POST
def create_room(request: HttpRequest) -> HttpResponse:
    """Create a room. Whoever creates it is its first member."""
    if not request.user.is_authenticated:
        return redirect("chat_lv2:index")
    name = request.POST.get("name", "").strip()
    if name:
        room = Room.objects.create(name=name)
        Membership.objects.create(room=room, user=request.user)
        return redirect("chat_lv2:room", room_id=room.id)
    return redirect("chat_lv2:index")


@require_POST
def join_room(request: HttpRequest, room_id: uuid.UUID) -> HttpResponse:
    """Become a member of a room, and go in."""
    if not request.user.is_authenticated:
        return redirect("chat_lv2:index")
    room = get_object_or_404(Room, id=room_id)
    Membership.objects.get_or_create(room=room, user=request.user)
    return redirect("chat_lv2:room", room_id=room.id)


@require_POST
def leave_room(request: HttpRequest, room_id: uuid.UUID) -> HttpResponse:
    """Stop being a member. Pages of the room this user still has open go back to the list."""
    if request.user.is_authenticated:
        room = get_object_or_404(Room, id=room_id)
        end_membership(room, request.user)
    return redirect("chat_lv2:index")


@require_POST
def sign_in(request: HttpRequest, username: str) -> HttpResponse:
    """Sign in as a demo user, creating them on first use. A demo, not a login."""
    if username not in DEMO_USERS:
        return redirect("chat_lv2:index")
    user, _ = get_user_model().objects.get_or_create(username=username)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect("chat_lv2:index")


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("chat_lv2:index")
