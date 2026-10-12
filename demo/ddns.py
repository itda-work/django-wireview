"""Direct mode's DDNS (demo/README.md "동적 IP"): keeps DEMO_HOST's A record on this line's address.

    python demo/ddns.py          # every DDNS_INTERVAL seconds (compose.direct.yaml's ddns profile)
    python demo/ddns.py --once   # one round, to try the token

A home line's address changes when the provider says so. Every round asks Cloudflare's trace
endpoint for the address this machine's requests leave from and, if the A record says another,
changes the record's content and nothing else: the record must already exist (made by hand, DNS
only), so this never creates a name, never touches a CNAME (the tunnel's), and never turns
Cloudflare's proxy on or off.

The token needs Zone -> DNS -> Edit on the demo's zone only (CLOUDFLARE_DNS_TOKEN, with
CLOUDFLARE_ZONE_ID, both from demo/ddns.env, which only this service reads). Standard library
only: it runs in the app image.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.cloudflare.com/client/v4"
#: Answers with the caller's address as "ip=…" among its lines
TRACE = "https://1.1.1.1/cdn-cgi/trace"

log = logging.getLogger("demo.ddns")


def public_address(opener=urllib.request.urlopen) -> str:
    with opener(TRACE, timeout=10) as response:
        lines = response.read().decode().splitlines()
    for line in lines:
        if line.startswith("ip="):
            address = ipaddress.ip_address(line[3:].strip())
            if address.version != 4:
                raise ValueError(f"the trace answered an IPv6 address ({address}); an A record wants IPv4")
            if not address.is_global:
                raise ValueError(f"the trace answered a non-public address ({address})")
            return str(address)
    raise ValueError("the trace answered no ip= line")


def _call(method: str, path: str, token: str, body: dict | None = None, opener=urllib.request.urlopen) -> dict:
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with opener(request, timeout=10) as response:
        answer = json.loads(response.read())
    if not answer.get("success"):
        raise RuntimeError(f"Cloudflare refused {method} {path}: {answer.get('errors')}")
    return answer


def sync(host: str, zone: str, token: str, opener=urllib.request.urlopen) -> str:
    """One round. Says what it did."""
    address = public_address(opener)
    query = urllib.parse.urlencode({"type": "A", "name": host})
    records = _call("GET", f"/zones/{zone}/dns_records?{query}", token, opener=opener)["result"]
    if len(records) != 1:
        raise RuntimeError(f"{host} has {len(records)} A records; make exactly one by hand (DNS only) first")
    record = records[0]
    if record["content"] == address:
        return f"{host} is {address}"
    _call("PATCH", f"/zones/{zone}/dns_records/{record['id']}", token, {"content": address}, opener=opener)
    return f"{host} moved from {record['content']} to {address}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="one round, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host = os.environ.get("DEMO_HOST", "")
    zone = os.environ.get("CLOUDFLARE_ZONE_ID", "")
    token = os.environ.get("CLOUDFLARE_DNS_TOKEN", "")
    if not (host and zone and token):
        log.error("DEMO_HOST, CLOUDFLARE_ZONE_ID and CLOUDFLARE_DNS_TOKEN are all needed")
        return 2
    interval = float(os.environ.get("DDNS_INTERVAL", "300"))
    last = None
    while True:
        try:
            said = sync(host, zone, token)
        except Exception as error:  # noqa: BLE001 -- a failed round waits for the next one
            log.warning("DDNS round failed: %s", error)
            if args.once:
                return 1
        else:
            # Quiet while nothing changes: one line when the answer differs from the last
            if said != last:
                log.info("%s", said)
            last = said
            if args.once:
                return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
