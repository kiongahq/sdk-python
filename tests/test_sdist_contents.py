"""The source distribution ships a declared list of files and nothing else."""
import importlib.util
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_sdist", ROOT / "scripts" / "check_sdist.py")
check_sdist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_sdist)


def _archive(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "kionga-0.0.0.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name in names:
            info = tarfile.TarInfo(f"kionga-0.0.0/{name}")
            tar.addfile(info, io.BytesIO(b""))
    return path


def test_declared_list_covers_the_package_and_its_tests():
    allowed = check_sdist.allowed_entries()
    assert {"kionga", "tests", "pyproject.toml", "README.md", "LICENCE", "PKG-INFO"} <= allowed
    # Repository plumbing is not part of the distribution.
    assert not {".github", "release-please-config.json", "version.txt"} & allowed


def test_every_declared_path_exists():
    for entry in check_sdist.allowed_entries() - {"PKG-INFO"}:
        assert (ROOT / entry).exists(), entry


def test_clean_archive_passes(tmp_path):
    archive = _archive(tmp_path, ["PKG-INFO", "pyproject.toml", "kionga/__init__.py", "tests/test_client.py"])
    assert check_sdist.unexpected_entries(archive, check_sdist.allowed_entries()) == []


def test_stray_files_are_reported(tmp_path):
    archive = _archive(tmp_path, ["kionga/__init__.py", ".github/workflows/ci.yml", "NOTES.md",
                                  ".env", "dist/old.whl"])
    extra = check_sdist.unexpected_entries(archive, check_sdist.allowed_entries())
    assert extra == [".env", ".github", "NOTES.md", "dist"]


def test_main_exit_codes(tmp_path, capsys):
    good = _archive(tmp_path, ["kionga/__init__.py"])
    assert check_sdist.main([str(good)]) == 0
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = _archive(bad_dir, ["kionga/__init__.py", "NOTES.md"])
    assert check_sdist.main([str(bad)]) == 1
    assert "NOTES.md" in capsys.readouterr().err
    assert check_sdist.main([]) == 2
