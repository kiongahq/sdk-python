"""kionga/_sync is generated from kionga/_async; editing it by hand would be lost."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_sync_client_is_generated_from_the_current_async_source():
    result = subprocess.run([sys.executable, "scripts/unasync.py", "--check"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
