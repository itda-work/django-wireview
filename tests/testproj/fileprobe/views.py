from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

#: What the external target received, by ref: stands in for a bucket.
RECEIVED: dict[str, bytes] = {}


@csrf_exempt
def put_target(request, ref: str):
    """The presigned URL's server: accepts the bytes the browser PUTs straight to it."""
    if request.method != "PUT":
        return HttpResponse(status=405)
    RECEIVED[ref] = request.body
    return HttpResponse(status=200)
