"""Copy the guest's result files into bench/results as win11-parlab-*.json with guest metadata.

python bench/windows/collect.py ~/parlab/out bench/results <sha>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main(src: Path, dst: Path, sha: str) -> None:
    machine = {}
    for venv, key in ((".venv", "arm64"), (".venv-x64", "x64")):
        f = src / f"machine-{venv}.json"
        if f.exists():
            machine[key] = json.loads(f.read_text(encoding="utf-8-sig"))
    for result in sorted(src.glob("*.json")):
        if result.name.startswith("machine-") or result.name == "smoke.json":
            continue
        data = json.loads(result.read_text())
        data["meta"]["sha"] = sha
        data["meta"]["ref"] = "heads/main"
        arch = "arm64" if result.name.startswith("arm64-") else "x64"
        data["meta"]["guest"] = {"vm": "Parallels Desktop win11-parlab", "python_build": arch, **machine.get(arch, {})}
        target = dst / f"win11-parlab-{result.name}"
        target.write_text(json.dumps(data, indent=1))
        print("wrote", target)
    progress = (src / "progress.txt").read_text(encoding="utf-8-sig", errors="replace")
    failed = [line.split()[0] for line in progress.splitlines() if line.strip() and " rc=0 " not in line]
    for name in failed:
        log = src / f"{name}.log"
        if log.exists():
            shutil.copy(log, dst / f"win11-parlab-{name}.failed.log")
            print("wrote", dst / f"win11-parlab-{name}.failed.log")


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser(), Path(sys.argv[2]), sys.argv[3])
