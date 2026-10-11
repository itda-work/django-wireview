"""A boundary that admits everyone, so the same moves can be made inside one (#209)."""

from wireview import live_session

#: No predicate: anonymous and logged-in visitors alike. What it adds is the
#: authentication fingerprint in every state its pages issue.
inside = live_session("ip-inside")
