"""Fail if a source distribution contains anything that should not ship.

    python scripts/check_sdist.py dist/kionga-*.tar.gz

Two rules, both checked for every file in the archive:

1. Its top-level entry is in `only-include` from pyproject.toml, or is one of
   the files the build backend always adds.
2. The file is in the checked-out commit. A file that exists only in a working
   tree or the index is never part of a release, even inside an included folder.

Links, duplicates, an empty archive and anything outside the single
`<name>-<version>` folder are rejected.

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


def expected_root(pyproject: Path = ROOT / "pyproject.toml") -> str:
    """The single folder a source distribution of this project must have."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return f"{project['name'].replace('-', '_')}-{project['version']}"


def problems(archive: Path, allowed: set[str], tracked: set[str], root: str | None = None) -> list[str]:
    """One line per rule the archive breaks, sorted.

    Every member must sit under the one distribution folder, appear once, and,
    unless it is a folder, be a regular file.
    """
    root = root or expected_root()
    found = []
    seen = set()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
    if not any(member.isreg() for member in members):
        found.append("archive holds no files")
    for member in members:
        top, _, path = member.name.partition("/")
        if top != root:
            found.append(f"{member.name}: outside the distribution folder {root}")
            continue
        if member.name in seen:
            found.append(f"{path}: appears more than once")
        seen.add(member.name)
        if member.isdir():
            if path in tracked:
                found.append(f"{path}: a folder where a file is expected")
            continue
        if not path:
            found.append(f"{member.name}: outside the distribution folder {root}")
        elif not member.isreg():
            found.append(f"{path}: not a regular file")
        elif path.split("/")[0] not in allowed:
            found.append(f"{path}: not in the declared list")
        elif path not in tracked and path not in GENERATED:
            found.append(f"{path}: not tracked by Git")
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
