import uuid

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import Room


def index(request: HttpRequest) -> HttpResponse:
    """List all chat rooms."""
    rooms = Room.objects.all()
    return render(request, "chat/index.html", {"rooms": rooms})


def room(request: HttpRequest, room_id: uuid.UUID) -> HttpResponse:
    """Display a chat room."""
    room = get_object_or_404(Room, id=room_id)

    # Ensure session exists for unique username generation
    if not request.session.session_key:
        request.session.create()

    # Generate a simple username (in production, use auth)
    session_key = request.session.session_key
    username = request.GET.get("username", f"User_{session_key[:6]}")
    return render(request, "chat/room.html", {"room": room, "username": username})


def create_room(request: HttpRequest) -> HttpResponse:
    """Create a new chat room."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if name:
            room = Room.objects.create(name=name)
            return redirect("chat:room", room_id=room.id)
    return redirect("chat:index")
