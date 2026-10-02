from django.shortcuts import render

# ?rows=1, ?live=1, ?notes=1, ?joined=1 and ?func=1: one component of live.py on a page of its own
ALONE = {
    "rows": "TempRows",
    "live": "TempLiveHost",
    "notes": "TempNotesProbe",
    "joined": "TempJoinedProbe",
    "func": "TempFuncProbe",
}


def index(request):
    for flag, name in ALONE.items():
        if request.GET.get(flag):
            return render(request, "tempprobe/alone.html", {"name": name})
    # ?nest=1: the probe drawn in a host's pass, and in another component's slot
    return render(request, "tempprobe/nest.html" if request.GET.get("nest") else "tempprobe/page.html")
