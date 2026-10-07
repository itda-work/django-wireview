# The runner image `make ci-local` (scripts/act_ci.py) hands act for ubuntu-24.04.
# act's own image lacks what a GitHub runner has and ci.yml leans on:
#   xvfb-run and the libraries Electron needs (vscode-extension-host),
#   nc (tests/e2e.sh asks whether a server already listens with `nc -z`),
#   systemctl (the redis-server step stops the service the package starts).
FROM catthehacker/ubuntu:act-24.04
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    xvfb xauth libgtk-3-0t64 libnss3 libxss1 libasound2t64 libgbm1 libsecret-1-0 libxkbfile1 netcat-openbsd >/dev/null \
 && rm -rf /var/lib/apt/lists/*
# No systemd in a container, so no service was started: stopping one succeeds.
RUN printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/systemctl && chmod +x /usr/local/bin/systemctl
