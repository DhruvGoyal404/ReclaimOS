"""Money is integer paise, always. These tests are the enforcement mechanism."""

from __future__ import annotations

import ast
import pathlib

import pytest

from reclaimos.money import Paise, format_inr, pct, rupees, to_rupee_str

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "reclaimos"


def test_rupees_from_string_is_exact() -> None:
    assert rupees("499.00") == 49900
    assert rupees("0.01") == 1
    assert rupees("1234.56") == 123456


def test_rupees_rejects_float() -> None:
    with pytest.raises(TypeError):
        rupees(499.00)  # type: ignore[arg-type]


def test_rupees_rounds_half_up_not_bankers() -> None:
    # Python's round() would give 2 here; ROUND_HALF_UP must give 3.
    assert rupees("0.025") == 3


def test_to_rupee_str_roundtrips() -> None:
    for value in ["0.00", "1.05", "999999.99"]:
        assert to_rupee_str(rupees(value)) == value


def test_format_inr_uses_indian_grouping() -> None:
    assert format_inr(Paise(12345678)) == "\u20b91,23,456.78"
    assert format_inr(Paise(49900)) == "\u20b9499.00"
    assert format_inr(Paise(-49900)) == "-\u20b9499.00"


def test_pct_handles_empty_denominator() -> None:
    assert pct(0, 0) == 0.0
    assert pct(1, 4) == 25.0


def test_no_float_literals_touch_money_fields() -> None:
    """Static guard: no source file may annotate a *_paise field as float.

    Cheap, but it has already earned its keep once -- see docs/failure-log.md.
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                annotation = ast.unparse(node.annotation)
                if name.endswith("_paise") and "float" in annotation:
                    offenders.append(f"{path.name}:{node.lineno} {name}: {annotation}")
    assert not offenders, f"money fields annotated as float: {offenders}"
