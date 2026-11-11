"""Notebooks are documentation people run against their own platform; keep them safe and runnable."""
import ast
import json
import re
from pathlib import Path

import pytest

import kionga

NOTEBOOKS = sorted((Path(__file__).resolve().parent.parent / "notebooks").glob("*.ipynb"))


def code_cells(path):
    return [c for c in json.loads(path.read_text())["cells"] if c["cell_type"] == "code"]


def source(cell):
    return "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_every_code_cell_is_valid_python(path):
    for cell in code_cells(path):
        ast.parse(source(cell))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_saved_outputs_have_no_errors_or_credentials(path):
    text = path.read_text()
    assert "Bearer " not in text and not re.search(r"KIONGA_TOKEN\s*=\s*['\"]", text)
    for cell in code_cells(path):
        assert not [o for o in cell.get("outputs", []) if o.get("output_type") == "error"]


def test_the_promotion_review_is_committed_as_a_dry_run():
    settings = next(source(c) for c in code_cells(Path(NOTEBOOKS[0]).parent / "eda.ipynb") if "PROMOTE" in source(c))
    assert re.search(r"^PROMOTE = False\b", settings, re.M)


def test_the_promotion_review_only_calls_methods_the_sdk_has():
    used = set()
    for cell in code_cells(Path(NOTEBOOKS[0]).parent / "eda.ipynb"):
        for node in ast.walk(ast.parse(source(cell))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "client":
                used.add(node.attr)
    assert used and all(hasattr(kionga.KiongaClient, name) for name in used), used
