"""Generate kionga/_sync from kionga/_async, the way httpcore does.

    python scripts/unasync.py          write kionga/_sync
    python scripts/unasync.py --check  fail if kionga/_sync is out of date

Edit kionga/_async only. Each rule is a whole-word replacement, applied in order.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "kionga"
SOURCE, TARGET = ROOT / "_async", ROOT / "_sync"

RULES = [
    (r"^import asyncio\n", ""),
    (r"\bAsyncKiongaClient\b", "KiongaClient"),
    (r"\bhttpx\.AsyncClient\b", "httpx.Client"),
    (r"\bhttpx\.AsyncBaseTransport\b", "httpx.BaseTransport"),
    (r"\bAsyncIterator\b", "Iterator"),
    (r"\basync def\b", "def"),
    (r"\basync for\b", "for"),
    (r"\basync with\b", "with"),
    (r"\bawait ", ""),
    (r"\b__aenter__\b", "__enter__"),
    (r"\b__aexit__\b", "__exit__"),
    (r"\baclose\b", "close"),
    (r"\baread\b", "read"),
    (r"\baiter_lines\b", "iter_lines"),
    (r"\basyncio\.sleep\b", "time.sleep"),
    (r"Async source of truth\. kionga/_sync is generated from this by scripts/unasync\.py\.",
     "Generated from kionga/_async by scripts/unasync.py. Do not edit."),
    (r"Async source of the HTTP client\. kionga/_sync is generated from it: scripts/unasync\.py\.",
     "Sync HTTP client, generated from kionga/_async by scripts/unasync.py. Do not edit."),
]


def unasync(text: str) -> str:
    for pattern, replacement in RULES:
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    return text


def main(check: bool) -> int:
    stale = []
    expected = {path.name: unasync(path.read_text()) for path in sorted(SOURCE.glob("*.py"))}
    for name, text in expected.items():
        target = TARGET / name
        if check:
            if not target.exists() or target.read_text() != text:
                stale.append(str(target.relative_to(ROOT.parent)))
        else:
            TARGET.mkdir(exist_ok=True)
            target.write_text(text)
    extra = {p.name for p in TARGET.glob("*.py")} - set(expected) if TARGET.exists() else set()
    if check and (stale or extra):
        print("kionga/_sync is out of date; run python scripts/unasync.py:", *stale, *sorted(extra), sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv[1:]))
