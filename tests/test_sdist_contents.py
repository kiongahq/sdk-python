"""The source distribution ships declared, tracked files and nothing else."""
import importlib.util
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_sdist", ROOT / "scripts" / "check_sdist.py")
check_sdist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_sdist)

ALLOWED = {"PKG-INFO", "pyproject.toml", "kionga", "tests"}
TRACKED = {"pyproject.toml", "kionga/__init__.py", "tests/test_client.py"}


def _archive(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "kionga-0.0.0.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name in names:
            tar.addfile(tarfile.TarInfo(f"kionga-0.0.0/{name}"), io.BytesIO(b""))
    return path


def test_declared_list_covers_the_package_and_its_tests():
    allowed = check_sdist.allowed_entries()
    assert {"kionga", "tests", "pyproject.toml", "README.md", "LICENCE", "PKG-INFO"} <= allowed
    # Repository plumbing is not part of the distribution.
    assert not {".github", "release-please-config.json", "version.txt"} & allowed


def test_every_declared_path_exists_and_is_tracked():
    tracked = check_sdist.tracked_files()
    tracked_tops = {path.split("/")[0] for path in tracked}
    for entry in check_sdist.allowed_entries() - check_sdist.GENERATED:
        assert (ROOT / entry).exists(), entry
        assert entry in tracked_tops, entry


def test_clean_archive_passes(tmp_path):
    archive = _archive(tmp_path, ["PKG-INFO", "pyproject.toml", "kionga/__init__.py", "tests/test_client.py"])
    assert check_sdist.problems(archive, ALLOWED, TRACKED) == []


def test_entries_outside_the_declared_list_are_reported(tmp_path):
    archive = _archive(tmp_path, ["kionga/__init__.py", ".github/workflows/ci.yml", "NOTES.md", ".env"])
    assert check_sdist.problems(archive, ALLOWED, TRACKED) == [
        ".env: not in the declared list",
        ".github/workflows/ci.yml: not in the declared list",
        "NOTES.md: not in the declared list",
    ]


def test_untracked_file_inside_an_included_folder_is_reported(tmp_path):
    archive = _archive(tmp_path, ["kionga/__init__.py", "tests/test_client.py", "tests/scratch.txt",
                                  "kionga/_local.py"])
    assert check_sdist.problems(archive, ALLOWED, TRACKED) == [
        "kionga/_local.py: not tracked by Git",
        "tests/scratch.txt: not tracked by Git",
    ]


def test_generated_metadata_needs_no_tracking(tmp_path):
    archive = _archive(tmp_path, ["PKG-INFO"])
    assert check_sdist.problems(archive, ALLOWED, set()) == []


def test_main_exit_codes(tmp_path, capsys):
    good = _archive(tmp_path, ["kionga/__init__.py", "pyproject.toml"])
    assert check_sdist.main([str(good)]) == 0
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = _archive(bad_dir, ["kionga/__init__.py", "tests/scratch.txt"])
    assert check_sdist.main([str(bad)]) == 1
    assert "tests/scratch.txt: not tracked by Git" in capsys.readouterr().err
    assert check_sdist.main([]) == 2
