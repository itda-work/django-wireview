import secrets

from django.shortcuts import render


def strict_policy(nonce: str) -> str:
    """No 'unsafe-inline' anywhere: an inline handler or an un-nonced <style> is a violation."""
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'nonce-{nonce}'; "
        "connect-src 'self'; img-src 'self' blob: data:"
    )


def index(request):
    # Set by hand rather than through Django's CSP middleware, which only exists
    # from 6.0: this fixture has to run on every Django the CI matrix tests. The
    # attribute is the one that middleware sets, so {% wireview_header %} reads it.
    nonce = secrets.token_urlsafe(16)
    request._csp_nonce = nonce
    response = render(request, "cspprobe/page.html")
    # ?csp=0 serves the same page without a policy: the not-live fallback has to
    # be checked where an inline handler, had there been one, would have run.
    if request.GET.get("csp") != "0":
        response["Content-Security-Policy"] = strict_policy(nonce)
    return response


def submitted(request):
    """Where the probe's form goes when the page is not live."""
    return render(request, "cspprobe/submitted.html", {"q": request.GET.get("q", "")})
