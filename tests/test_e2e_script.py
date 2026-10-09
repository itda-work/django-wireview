"""tests/e2e.sh: what it hands pytest, and the broker it runs the suite on.

The script always put ``tests examples`` in front of its arguments, so
``./tests/e2e.sh tests/test_streams_e2e.py`` collected every E2E suite and ran
them all. And on the redis layer it started no server, so runs started one by
hand and one was left running (#171). These tests run the script with ``uv``
swapped for a stub that records what it was asked to run and what it found.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import time
import tomllib
import typing as t
from pathlib import Path

import pytest
from testproj.server_process import free_port, stop_process
from testproj.time_limit import disown, own

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.unit

DEFAULTS = ["tests", "examples"]


def run_script(tmp_path: Path, *args: str) -> list[str]:
    """The arguments tests/e2e.sh hands ``uv run pytest`` for ``args``."""
    record = tmp_path / "argv"
    stub = tmp_path / "uv"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > {record}\n')
    os.chmod(stub, 0o755)
    subprocess.run(
        ["bash", str(ROOT / "tests" / "e2e.sh"), *args],
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "WIREVIEW_TEST_LAYER": "memory"},
        check=True,
        capture_output=True,
        timeout=60,
    )
    argv = record.read_bytes().split(b"\0")[:-1]
    assert [a.decode() for a in argv[:2]] == ["run", "pytest"]
    return [a.decode() for a in argv[2:]]


def paths_in(argv: list[str]) -> list[str]:
    """The positional arguments pytest gets ahead of its options."""
    return argv[: argv.index("-m")]


def test_without_a_path_the_defaults_run(tmp_path):
    assert paths_in(run_script(tmp_path)) == DEFAULTS


def test_options_and_their_values_are_not_paths(tmp_path):
    # `-k tests` names a directory that exists, as a keyword
    argv = run_script(tmp_path, "-k", "tests or streams", "-x", "-o", "test_time_limit=0", "-k", "tests")
    assert paths_in(argv) == DEFAULTS
    assert argv[-7:] == ["-k", "tests or streams", "-x", "-o", "test_time_limit=0", "-k", "tests"]


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_streams_e2e.py",
        "examples/todo",
        "tests/test_streams_e2e.py::test_dom_id_names_each_item",
        "tests/test_streams_e2e.py::test_dom_id_names_each_item[chromium]",
    ],
)
def test_a_path_replaces_the_defaults(tmp_path, path):
    argv = run_script(tmp_path, "-k", "dom_id", path)
    assert paths_in(argv) == []
    assert argv[-3:] == ["-k", "dom_id", path]


def test_an_option_value_taken_for_a_path_still_runs_every_e2e_suite():
    # `--cov wireview` drops the defaults (the script lists only some options
    # that take a value). That is safe while pytest collects from the rootdir:
    # with no `testpaths`, the rootdir holds the same suites as `tests examples`.
    options = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert "testpaths" not in options, "tests/e2e.sh: an option value taken for a path now narrows the run"


# --- The redis layer's broker (#171) -----------------------------------------
#
# The script used to stop at "the redis layer needs a redis-server", so every run
# started one by hand, and one was left running. It now treats redis as it treats
# nats: a server already on REDIS_URL is used and left alone, and otherwise a
# throwaway one is started and stopped however the suite ends.

#: Stands in for ``uv run pytest``: writes the REDIS_URL it got and whether a redis
#: answered PING there while the suite ran, then waits or fails as the test asks.
#:
#: The record is written by the shell once python3 has exited, not by python3. A test
#: interrupts the stub as soon as the record has something in it, and python3 writes
#: its output while it shuts down: a SIGINT landing then is no longer checked, python3
#: exits 0, and bash goes on with the next command when its foreground child did not
#: die of the signal. The stub then slept out STUB_SLEEP and the script waited on it.
#:
#: For the same reason the sleep is not a foreground child either (#192). A SIGINT that
#: landed after bash forked it and before it ran ``sleep`` reached bash's own handler in
#: the child, which then slept the whole STUB_SLEEP; bash, whose child had not died of the
#: signal, waited for it. The sleep runs in the background (where SIGINT is ignored
#: anyway) and the stub waits for it with the ``wait`` builtin, which a SIGINT ends. Its
#: EXIT trap stops the sleep. STUB_DEAF makes the sleep ignore SIGINT outright, which is
#: what that window did, so the case is run on purpose rather than by luck.
REDIS_STUB = """#!/usr/bin/env bash
seen="$(python3 - "$REDIS_URL" <<'PY'
import socket, sys, urllib.parse
url = urllib.parse.urlsplit(sys.argv[1])
try:
    with socket.create_connection((url.hostname, url.port), timeout=2) as s:
        s.sendall(b"PING\\r\\n")
        answered = s.recv(16).startswith(b"+PONG")
except OSError:
    answered = False
print(sys.argv[1], answered)
PY
)"
if [ -n "${{STUB_SLEEP:-}}" ]; then
  if [ -n "${{STUB_DEAF:-}}" ]; then
    (trap '' INT; exec sleep "$STUB_SLEEP") </dev/null >/dev/null 2>&1 &
  else
    sleep "$STUB_SLEEP" </dev/null >/dev/null 2>&1 &
  fi
  sleeper=$!
  trap 'kill "$sleeper" 2>/dev/null' EXIT
fi
printf '%s\\n' "$seen" > {record}
[ -z "${{STUB_SLEEP:-}}" ] || wait "$sleeper"
exit "${{STUB_EXIT:-0}}"
"""


def _missing(reason: str) -> t.NoReturn:
    """Skip on a developer's machine; fail where CI runs, as tests/test_nats_layer.py does."""
    if os.environ.get("CI", "").lower() not in ("", "0", "false"):
        pytest.fail(f"{reason}, and CI must run these tests", pytrace=False)
    pytest.skip(reason)


@pytest.fixture
def redis_binary() -> str:
    """The redis-server the script would find, in its order."""
    for candidate in ("redis-server", "/opt/homebrew/bin/redis-server", "/usr/local/bin/redis-server"):
        if found := shutil.which(candidate):
            return found
    _missing("redis-server binary not found")


def listening(port: int) -> bool:
    """What the script asks before it starts a server: does anything accept on the port."""
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def redis_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            s.sendall(b"PING\r\n")
            return s.recv(16).startswith(b"+PONG")
    except OSError:
        return False


class RedisRun(t.NamedTuple):
    returncode: int
    output: str
    #: The URL the suite got and whether a redis answered there, or None if it never ran.
    seen: tuple[str, bool] | None
    #: The pid of the server the script started, if it says it started one.
    started: int | None


def start_redis_script(tmp_path: Path, **env: str) -> tuple[subprocess.Popen, Path]:
    record = tmp_path / "seen"
    stub = tmp_path / "bin" / "uv"
    stub.parent.mkdir()
    stub.write_text(REDIS_STUB.format(record=record))
    os.chmod(stub, 0o755)
    (tmp_path / "tmp").mkdir()
    base = {k: v for k, v in os.environ.items() if k not in ("REDIS_URL", "REDIS_SERVER")}
    proc = subprocess.Popen(
        ["bash", str(ROOT / "tests" / "e2e.sh")],
        env={
            **base,
            "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}",
            "WIREVIEW_TEST_LAYER": "redis",
            "TMPDIR": str(tmp_path / "tmp"),
            **env,
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return own(proc), record


def finish_redis_script(proc: subprocess.Popen, record: Path, timeout: float = 30) -> RedisRun:
    stuck = False
    try:
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # The script's whole group, the server it started included: it is not stopping.
            stuck = True
            os.killpg(proc.pid, signal.SIGKILL)
            output, _ = proc.communicate()
    finally:
        disown(proc)
    if stuck:
        pytest.fail(f"tests/e2e.sh was still running after {timeout}s:\n{output}", pytrace=False)
    seen = None
    if record.exists():
        url, answered = record.read_text().split()
        seen = (url, answered == "True")
    started = re.search(r"started \S+ \(pid (\d+)\)", output)
    return RedisRun(proc.returncode, output, seen, int(started[1]) if started else None)


def run_redis_script(tmp_path: Path, **env: str) -> RedisRun:
    return finish_redis_script(*start_redis_script(tmp_path, **env))


def assert_gone(run: RedisRun, tmp_path: Path) -> None:
    """The server the script started is no longer running, and its directory is gone."""
    assert run.started is not None, run.output
    with pytest.raises(ProcessLookupError):
        os.kill(run.started, 0)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_without_a_server_it_starts_one_on_a_free_port_and_stops_it(tmp_path, redis_binary):
    if listening(6379):
        pytest.skip("something is on 6379, so the script uses it instead")
    run = run_redis_script(tmp_path, REDIS_SERVER=redis_binary)
    assert run.returncode == 0, run.output
    url, answered = run.seen
    assert answered, run.output
    # A free port, not 6379: a run elsewhere would take a throwaway there for its own.
    assert url.startswith("redis://127.0.0.1:") and not url.endswith(":6379")
    assert_gone(run, tmp_path)
    assert not redis_answers(int(url.rsplit(":", 1)[1]))


def test_a_named_local_url_without_a_server_gets_one_there(tmp_path, redis_binary):
    port = free_port()
    url = f"redis://localhost:{port}/3"
    run = run_redis_script(tmp_path, REDIS_URL=url, REDIS_SERVER=redis_binary)
    assert run.returncode == 0, run.output
    assert run.seen == (url, True)
    assert_gone(run, tmp_path)
    assert not redis_answers(port)


def test_a_server_already_running_is_used_and_left_running(tmp_path, redis_binary):
    port = free_port()
    server = own(
        subprocess.Popen(
            [redis_binary, "--bind", "127.0.0.1", "--port", str(port), "--save", "", "--appendonly", "no"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=tmp_path,
        )
    )
    try:
        deadline = time.monotonic() + 15
        while not redis_answers(port):
            assert server.poll() is None and time.monotonic() < deadline, "the test's redis-server did not come up"
            time.sleep(0.05)
        url = f"redis://127.0.0.1:{port}"
        run = run_redis_script(tmp_path, REDIS_URL=url, REDIS_SERVER=redis_binary)
        assert run.returncode == 0, run.output
        assert f"using the redis-server already on {url}" in run.output
        assert run.seen == (url, True)
        assert run.started is None
        assert redis_answers(port), "the script stopped a server it did not start"
    finally:
        stop_process(server)


def test_a_failing_suite_still_stops_the_server(tmp_path, redis_binary):
    run = run_redis_script(
        tmp_path, REDIS_SERVER=redis_binary, REDIS_URL=f"redis://127.0.0.1:{free_port()}", STUB_EXIT="3"
    )
    assert run.returncode == 3, run.output
    assert run.seen[1], run.output
    assert_gone(run, tmp_path)


@pytest.mark.parametrize("deaf", [False, True], ids=["sleeping", "deaf"])
def test_an_interrupted_suite_still_stops_the_server(tmp_path, redis_binary, deaf):
    """``deaf``: the suite's process does not die of the SIGINT, as a sleep forked a moment
    before it did not (#192), and the script still ends at once."""
    proc, record = start_redis_script(
        tmp_path,
        REDIS_SERVER=redis_binary,
        REDIS_URL=f"redis://127.0.0.1:{free_port()}",
        STUB_SLEEP="60",
        **({"STUB_DEAF": "1"} if deaf else {}),
    )
    deadline = time.monotonic() + 20
    while not record.exists() or not record.read_text():
        assert proc.poll() is None and time.monotonic() < deadline, "the suite never started"
        time.sleep(0.05)
    os.killpg(proc.pid, signal.SIGINT)  # what Ctrl-C sends: the whole foreground group
    # Well inside the time limit and STUB_SLEEP: a script that outlives Ctrl-C fails here.
    run = finish_redis_script(proc, record, timeout=20)
    assert run.returncode == -signal.SIGINT, run.output  # bash dies of the signal, after its EXIT trap
    assert run.seen[1], run.output
    assert_gone(run, tmp_path)


def test_without_the_binary_it_says_how_to_get_one(tmp_path):
    missing = tmp_path / "no-redis-server"
    run = run_redis_script(tmp_path, REDIS_SERVER=str(missing), REDIS_URL=f"redis://127.0.0.1:{free_port()}")
    assert run.returncode == 1
    assert "the redis layer needs a redis-server and none was found" in run.output
    assert "brew install redis" in run.output
    assert f"REDIS_SERVER={missing} is not an executable" in run.output
    assert run.seen is None


def test_a_server_is_started_only_on_this_machine(tmp_path):
    # .invalid never resolves (RFC 6761), so nothing answers there, at once.
    run = run_redis_script(tmp_path, REDIS_URL="redis://broker.invalid:6379")
    assert run.returncode == 1
    assert "a redis-server is started only on this machine" in run.output
    assert run.seen is None
