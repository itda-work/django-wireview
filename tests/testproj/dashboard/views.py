from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def index(request: HttpRequest) -> HttpResponse:
    """Display the dashboard."""
    return render(request, "dashboard/index.html")
