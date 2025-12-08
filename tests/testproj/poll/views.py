from django.shortcuts import get_object_or_404, render

from .models import Poll


def index(request):
    """List all active polls."""
    polls = Poll.objects.filter(is_active=True)
    return render(request, "poll/index.html", {"polls": polls})


def poll_detail(request, poll_id):
    """Show a single poll for voting."""
    poll = get_object_or_404(Poll, id=poll_id)
    return render(request, "poll/detail.html", {"poll": poll})
