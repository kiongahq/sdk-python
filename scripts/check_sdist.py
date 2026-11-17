"""Fail if a source distribution contains anything outside the declared list.

    python scripts/check_sdist.py dist/kionga-*.tar.gz

The allowed top-level entries are `only-include` from pyproject.toml plus the
files the build backend always adds. Run in CI after `python -m build`, so a
file that is not meant to ship cannot reach a release.
"""
import sys
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Added by hatchling whatever the include list says.
ALWAYS = {"PKG-INFO", "pyproject.toml", "README.md", "LICENCE", ".gitignore"}


def allowed_entries(pyproject: Path = ROOT / "pyproject.toml") -> set[str]:
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    sdist = config["tool"]["hatch"]["build"]["targets"]["sdist"]
    return ALWAYS | {entry.strip("/").split("/")[0] for entry in sdist["only-include"]}


def unexpected_entries(archive: Path, allowed: set[str]) -> list[str]:
    """Top-level entries of the archive that are not allowed, sorted."""
    found = set()
    with tarfile.open(archive) as tar:
        for name in tar.getnames():
            parts = name.split("/")
            if len(parts) > 1 and parts[1]:
                found.add(parts[1])
    return sorted(found - allowed)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_sdist.py SDIST [SDIST ...]", file=sys.stderr)
        return 2
    allowed = allowed_entries()
    status = 0
    for name in argv:
        extra = unexpected_entries(Path(name), allowed)
        if extra:
            print(f"{name}: not in the declared list: {', '.join(extra)}", file=sys.stderr)
            status = 1
        else:
            print(f"{name}: ok")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
