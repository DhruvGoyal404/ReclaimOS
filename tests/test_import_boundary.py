"""The wall between the sealed world and everything that reasons about records.

This file is a load-bearing guarantee, not hygiene. If the Phase 3 propensity
table ever imports a probability constant from the world model -- even one -- then
the policy is scored against a world built from the same numbers, precision and
recall become circular, and every figure in EVAL.md is theatre (ADR-0006).

Today no classifier exists, so nothing can violate this. That is exactly why the
test is written now: the guarantee has to predate the code it constrains.

Two separate rules are enforced:

1. Only a named allow-list of modules may import the generator package at all.
   Every future module -- ``diagnose/``, ``policy/``, ``agent/`` -- is denied by
   default, because the allow-list is explicit rather than pattern-matched.

2. No module outside ``outcome_model`` itself may so much as *name* a world
   probability constant. This catches the re-export loophole: ``generator/__init__``
   surfaces ``success_probability``, so an import-path check alone would not be
   enough.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "reclaimos"

#: The only modules permitted to touch the sealed world, and -- where it can be
#: narrowed -- exactly which names each may take. ``None`` means unrestricted.
#:
#: ``reclaimos.cli`` is on the list only because ``gen`` has to build a dataset,
#: so it is pinned to that one entry point. It orchestrates generation; it never
#: reasons about a record. A blanket module-level allowance there would quietly
#: open the door for every future CLI command.
TRUTH_READERS: dict[str, frozenset[str] | None] = {
    "reclaimos.generator": None,
    "reclaimos.eval.harness": None,
    "reclaimos.eval.runner": None,
    "reclaimos.cli": frozenset({"build_dataset"}),
}

#: Names that encode the world's response surface. A policy that reads any of
#: these is being graded against its own assumptions.
WORLD_CONSTANTS: frozenset[str] = frozenset(
    {
        "RETRY_BASE",
        "LINK_RECEPTIVITY",
        "CARD_UPDATE_FIT",
        "CARD_UPDATE_FIT_DEFAULT",
        "ATTEMPT_DECAY",
        "CONTACT_DECAY",
        "METHOD_MULTIPLIER",
        "MAX_PROBABILITY",
        "success_probability",
        "_timing_multiplier",
        "_customer_multiplier",
    }
)


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _source_modules() -> list[tuple[str, Path]]:
    return sorted((_module_name(p), p) for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _allowance(module: str) -> tuple[bool, frozenset[str] | None]:
    """Return (is_allowed, permitted_symbols) for a module."""
    for allowed, symbols in TRUTH_READERS.items():
        if module == allowed or module.startswith(allowed + "."):
            return True, symbols
    return False, None


def test_only_the_allow_list_may_import_the_sealed_world() -> None:
    offenders: list[str] = []
    for module, path in _source_modules():
        allowed, permitted = _allowance(module)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "reclaimos.generator"
            ):
                if not allowed:
                    offenders.append(f"{module}:{node.lineno} imports {node.module}")
                elif permitted is not None:
                    for alias in node.names:
                        if alias.name not in permitted:
                            offenders.append(
                                f"{module}:{node.lineno} imports {alias.name}, "
                                f"but is limited to {sorted(permitted)}"
                            )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("reclaimos.generator") and not allowed:
                        offenders.append(f"{module}:{node.lineno} imports {alias.name}")
    assert not offenders, "modules outside the allow-list reached into the sealed world: " + str(
        offenders
    )


def test_no_module_outside_the_world_model_names_a_probability_constant() -> None:
    """Catches the re-export loophole an import-path check would miss."""
    offenders: list[str] = []
    for module, path in _source_modules():
        if module == "reclaimos.generator.outcome_model":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in WORLD_CONSTANTS and module != "reclaimos.generator":
                        offenders.append(f"{module}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.Attribute) and node.attr in WORLD_CONSTANTS:
                offenders.append(f"{module}:{node.lineno} references .{node.attr}")
    assert not offenders, "world probability constants leaked into policy code: " + str(offenders)


def test_the_probability_surface_lives_in_exactly_one_file() -> None:
    """If these constants are ever split across files, the rule above rots."""
    homes: dict[str, list[str]] = {}
    for module, path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            elif isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.FunctionDef):
                targets = [node.name]
            for target in targets:
                if target in WORLD_CONSTANTS:
                    homes.setdefault(target, []).append(module)

    for name, modules in homes.items():
        assert modules == ["reclaimos.generator.outcome_model"], (
            f"{name} is defined in {modules}. Two identically-named constants either "
            "side of the world/policy boundary are how a copy sneaks in unnoticed -- "
            "rename the policy-side one so the distinction stays visible."
        )


def test_the_decline_taxonomy_carries_no_probabilities() -> None:
    """The one module the generator and the future classifier legitimately share.

    It maps gateway tuples to a class -- vocabulary and emission, nothing more.
    The moment a probability appears in it, the classifier and the world would be
    reading the same number and the benchmark would close on itself.
    """
    source = (SRC / "domain" / "decline_codes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(
                f"decline_codes.py:{node.lineno} contains a float ({node.value}); "
                "the taxonomy must stay probability-free"
            )


def test_every_module_imports_cleanly_on_its_own() -> None:
    """Guards against import cycles the test suite's own order would hide.

    `from reclaimos.policy import AgentConfig` once raised ImportError while the
    whole suite passed, because pytest happened to import `reclaimos.eval` first
    and broke the cycle by accident. Importing each module in a fresh interpreter
    is the only way to catch that. See docs/failure-log.md.
    """
    import subprocess
    import sys

    modules = sorted(name for name, _ in _source_modules())
    result = subprocess.run(
        [sys.executable, "-c", "".join(f"import {m}\n" for m in modules)],
        cwd=SRC.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    for module in modules:
        one = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=SRC.parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        assert one.returncode == 0, f"{module} does not import on its own:\n{one.stderr}"
