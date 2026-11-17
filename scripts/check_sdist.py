"""Fail if a source distribution contains anything that should not ship.

    python scripts/check_sdist.py dist/kionga-*.tar.gz

Two rules, both checked for every file in the archive:

1. Its top-level entry is in `only-include` from pyproject.toml, or is one of
   the files the build backend always adds.
2. The file is in the checked-out commit. A file that exists only in a working
   tree or the index is never part of a release, even inside an included folder.

Links, and anything outside the single distribution folder, are rejected.

Run in CI after `python -m build`, before the archive is uploaded.
"""
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Added by hatchling whatever the include list says.
ALWAYS = {"PKG-INFO", "pyproject.toml", "README.md", "LICENCE", ".gitignore"}
# Written by the build backend; not a file in the repository.
GENERATED = {"PKG-INFO"}


def allowed_entries(pyproject: Path = ROOT / "pyproject.toml") -> set[str]:
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    sdist = config["tool"]["hatch"]["build"]["targets"]["sdist"]
    return ALWAYS | {entry.strip("/").split("/")[0] for entry in sdist["only-include"]}


def tracked_files(root: Path = ROOT) -> set[str]:
    """Every path in the checked-out commit, relative to the repository root.

    Read from HEAD, not the index, so a file that is only staged does not count.
    """
    listing = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", "HEAD"],
                             check=True, capture_output=True, text=True).stdout
    return {path for path in listing.split("\0") if path}


def problems(archive: Path, allowed: set[str], tracked: set[str]) -> list[str]:
    """One line per archive member that breaks a rule, sorted.

    Every member must sit under the single distribution folder, and every
    member that is not a folder must be a regular file.
    """
    found = []
    roots = set()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
    for member in members:
        root, _, path = member.name.partition("/")
        roots.add(root)
        if member.isdir():
            continue
        if not path:
            found.append(f"{member.name}: outside the distribution folder")
        elif not member.isreg():
            found.append(f"{path}: not a regular file")
        elif path.split("/")[0] not in allowed:
            found.append(f"{path}: not in the declared list")
        elif path not in tracked and path not in GENERATED:
            found.append(f"{path}: not tracked by Git")
    if len(roots) > 1:
        found.append(f"more than one top-level folder: {', '.join(sorted(roots))}")
    return sorted(found)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_sdist.py SDIST [SDIST ...]", file=sys.stderr)
        return 2
    allowed, tracked = allowed_entries(), tracked_files()
    status = 0
    for name in argv:
        found = problems(Path(name), allowed, tracked)
        for line in found:
            print(f"{name}: {line}", file=sys.stderr)
        if found:
            status = 1
        else:
            print(f"{name}: ok")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
