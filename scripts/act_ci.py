"""Run .github/workflows/ci.yml locally with act, one job and one matrix cell at a time (``make ci-local``).

CI runs only when dispatched by hand, so this is how a change gets the whole workflow before a push.
act's defaults differ from a GitHub runner where ci.yml leans on it; .actrc and .github/act/Dockerfile
close the gaps, and this script does what neither can:

- Builds the runner image .actrc names (``wireview-act-runner``) from .github/act/Dockerfile.
- Points act at the Docker daemon the ``docker`` CLI uses. act reads only DOCKER_HOST and otherwise
  assumes /var/run/docker.sock, which colima does not provide on the Mac.
- Runs each matrix cell on its own. The cells of one act run share a daemon: ``docker create --name
  nats-server`` and the E2E services' host ports collide when two run at once, which never happens on
  GitHub, where each cell has a machine of its own.

.actrc gives the job container an init (``--init``): act keeps it alive with ``tail -f /dev/null`` as
pid 1, which never reaps, so a process killed after its parent died stays a zombie that ``kill(pid, 0)``
still finds.
"""

from __future__ import annotations

import argparse
import itertools
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
IMAGE = "wireview-act-runner"
DOCKERFILE = ROOT / ".github" / "act" / "Dockerfile"
LOGS = ROOT / "build" / "act"


@dataclass(frozen=True)
class Cell:
    job: str
    matrix: tuple[tuple[str, str], ...] = ()

    @property
    def name(self) -> str:
        if not self.matrix:
            return self.job
        return f"{self.job}[{','.join(f'{key}={value}' for key, value in self.matrix)}]"

    def args(self) -> list[str]:
        args = ["-j", self.job]
        for key, value in self.matrix:
            args += ["--matrix", f"{key}:{value}"]
        return args


def cells(workflow: Path = WORKFLOW, jobs: list[str] | None = None) -> list[Cell]:
    """Every job of the workflow, a job with a matrix once per combination, in the file's order."""
    defined = yaml.safe_load(workflow.read_text())["jobs"]
    unknown = sorted(set(jobs or []) - set(defined))
    if unknown:
        raise SystemExit(f"no such job in {workflow.name}: {', '.join(unknown)}")
    found: list[Cell] = []
    for job, spec in defined.items():
        if jobs and job not in jobs:
            continue
        matrix = (spec.get("strategy") or {}).get("matrix") or {}
        if not matrix:
            found.append(Cell(job))
            continue
        if "include" in matrix or "exclude" in matrix:
            raise SystemExit(f"{job}: include and exclude are not expanded here; add them to scripts/act_ci.py")
        keys = list(matrix)
        for values in itertools.product(*(matrix[key] for key in keys)):
            found.append(Cell(job, tuple(zip(keys, (str(value) for value in values)))))
    return found


def docker_host() -> str:
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    context = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return context.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.act_ci", description="Run ci.yml locally with act, one job and matrix cell at a time."
    )
    parser.add_argument("jobs", nargs="*", help="jobs to run (default: every job in ci.yml)")
    parser.add_argument("--list", action="store_true", help="print the runs and exit")
    parser.add_argument("--no-build", action="store_true", help=f"use the {IMAGE} image already built")
    args = parser.parse_args(argv)

    runs = cells(jobs=args.jobs)
    if args.list:
        print("\n".join(cell.name for cell in runs))
        return 0

    env = {**os.environ, "DOCKER_HOST": docker_host()}
    if not args.no_build:
        subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-f", DOCKERFILE, DOCKERFILE.parent], env=env, check=True)

    LOGS.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for cell in runs:
        log = LOGS / f"{cell.name}.log"
        start = time.monotonic()
        with log.open("w") as out:
            # .actrc (the workflow, the image, --init, the daemon socket) is read from the working directory.
            code = subprocess.run(
                ["act", "workflow_dispatch", *cell.args()], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT
            ).returncode
        verdict = "ok" if code == 0 else f"FAILED (exit {code})"
        print(f"{cell.name:<40} {verdict:<18} {time.monotonic() - start:6.0f}s  {log.relative_to(ROOT)}", flush=True)
        if code != 0:
            failed.append(cell.name)

    print(f"\n{len(runs) - len(failed)} of {len(runs)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
