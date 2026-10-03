"""Pack a built site into docs-site-<tag>.tar.gz, the release asset (#160).

The archive holds ``wireview/...`` exactly as ``build`` wrote it, so unpacking it where
itda.work serves from puts every file at its URL. The tag is the one the build wrote to
``wireview/VERSION``, which ``build`` takes from pyproject.toml.

Two packings of one commit are the same bytes, whichever tar the machine has: members are
sorted by name, carry the commit's time, uid/gid 0 and no owner names, files are 0644 and
directories 0755, and the gzip header has mtime 0 and no file name. That is what lets a
rebuild of the tagged commit be compared with the asset on the release.

The members' time is ``SOURCE_DATE_EPOCH`` when it is set (the reproducible-builds
convention), otherwise the commit's (``git log -1 --format=%ct``). Not 0: a server that
serves the files as unpacked (Caddy, Go's ServeContent) takes the epoch for "unknown" and
sends neither Last-Modified nor an ETag (#166). With neither -- a tree unpacked from ``git
archive``, say -- packing stops and asks for ``SOURCE_DATE_EPOCH`` rather than guess a time.

What itda.work relies on in the bundle is written down in docs/implementation/docs-site-bundle.md.

The bundle goes under build/site-dist/, never dist/: the release uploads dist/ to PyPI whole.
"""

from __future__ import annotations

import gzip
import io
import os
import subprocess
import tarfile
from pathlib import Path

from . import nav
from .build import DEFAULT_OUT

DEFAULT_BUNDLE_DIR = nav.ROOT / "build" / "site-dist"

#: What PyPI receives; the bundle must not land in it.
PYPI_DIST = nav.ROOT / "dist"

TOP = "wireview"


def bundle_name(tag: str) -> str:
    return f"docs-site-{tag}.tar.gz"


def site_tag(site: Path = DEFAULT_OUT) -> str:
    """The tag the build in ``site`` was made for, from its VERSION file."""
    version_file = site / TOP / "VERSION"
    if not version_file.is_file():
        raise FileNotFoundError(f"{version_file} is missing: run `make docs-site` first")
    return version_file.read_text(encoding="utf-8").strip()


def source_date_epoch(root: Path = nav.ROOT) -> int:
    """The members' time: ``SOURCE_DATE_EPOCH``, or the time of the commit checked out at ``root``."""
    if value := os.environ.get("SOURCE_DATE_EPOCH", "").strip():
        if not value.isdigit():
            raise ValueError(f"SOURCE_DATE_EPOCH={value!r} is not a number of seconds")
        return int(value)
    try:
        found = subprocess.run(
            ["git", "log", "-1", "--format=%ct"], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        found = None
    if found is None or found.returncode or not found.stdout.strip().isdigit():
        raise RuntimeError(f"{root} has no commit to take a time from: set SOURCE_DATE_EPOCH")
    return int(found.stdout.strip())


def _info(name: str, path: Path, mtime: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
    else:
        info.type = tarfile.REGTYPE
        info.mode = 0o644
        info.size = path.stat().st_size
    return info


def pack(site: Path, mtime: int) -> bytes:
    """The archive's bytes: ``site``'s ``wireview/`` tree, normalised, every member at ``mtime``."""
    top = site / TOP
    if not top.is_dir():
        raise FileNotFoundError(f"{top} is missing: run `make docs-site` first")
    paths = [top, *top.rglob("*")]
    for path in paths:
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f"{path} is not a plain file or directory")
    members = sorted((path.relative_to(site).as_posix(), path) for path in paths)

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, path in members:
            info = _info(name, path, mtime)
            if info.isdir():
                archive.addfile(info)
            else:
                with path.open("rb") as stream:
                    archive.addfile(info, stream)

    packed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=packed, mtime=0, compresslevel=9) as stream:
        stream.write(tar_bytes.getvalue())
    return packed.getvalue()


def bundle(site: Path = DEFAULT_OUT, out_dir: Path = DEFAULT_BUNDLE_DIR, mtime: int | None = None) -> Path:
    """Write ``docs-site-<tag>.tar.gz`` for the build in ``site`` into ``out_dir``; return its path.

    ``mtime`` is the members' time; by default ``source_date_epoch()``.
    """
    out_dir = out_dir.resolve()
    if out_dir == PYPI_DIST or PYPI_DIST in out_dir.parents:
        raise ValueError(f"{out_dir} is inside dist/, which the release uploads to PyPI whole")
    tag = site_tag(site)
    data = pack(site, source_date_epoch() if mtime is None else mtime)
    out_dir.mkdir(parents=True, exist_ok=True)
    # One bundle in the directory, as `uv build --clear` leaves one build in dist/: the
    # release uploads every docs-site-*.tar.gz it finds there.
    for stale in out_dir.glob(bundle_name("*")):
        stale.unlink()
    target = out_dir / bundle_name(tag)
    target.write_bytes(data)
    return target
