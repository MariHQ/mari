"""Executable dependency rules for the layered Python package."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1] / "mari_server"

PERSISTENCE_FORBIDDEN = {"fastapi", "strawberry"}


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def violates(name: str, forbidden: set[str]) -> bool:
    return any(name == item or name.startswith(item + ".") for item in forbidden)


class ArchitectureTests(unittest.TestCase):
    def assert_layer(self, layer: str, forbidden: set[str]) -> None:
        failures = []
        for path in sorted((ROOT / layer).glob("*.py")):
            bad = sorted(name for name in imports(path) if violates(name, forbidden))
            if bad:
                failures.append(f"{path.relative_to(ROOT)}: {', '.join(bad)}")
        self.assertEqual(failures, [], "invalid outward dependencies:\n" + "\n".join(failures))

    def test_core_models_are_pure(self) -> None:
        failures = []
        paths = [
            ROOT / "identity" / "context.py",
            ROOT / "identity" / "actor.py",
            ROOT / "product" / "navigation.py",
        ]
        for path in paths:
            bad = sorted(name for name in imports(path)
                         if name.split(".")[0] not in sys.stdlib_module_names
                         and not name.startswith("mari_server.identity"))
            if bad:
                failures.append(f"{path.relative_to(ROOT)}: {', '.join(bad)}")
        self.assertEqual(failures, [], "core models must be standard-library-only:\n" + "\n".join(failures))

    def test_persistence_does_not_own_transports(self) -> None:
        failures = []
        for path in sorted((ROOT / "persistence").rglob("*.py")):
            bad = sorted(name for name in imports(path) if violates(name, PERSISTENCE_FORBIDDEN))
            if bad:
                failures.append(f"{path.relative_to(ROOT)}: {', '.join(bad)}")
        self.assertEqual(failures, [], "persistence must not own transports:\n" + "\n".join(failures))

    def test_retired_layer_buckets_do_not_return(self) -> None:
        retired = [
            name for name in ("api", "application", "domain", "infrastructure", "integrations", "repositories", "services")
            if (ROOT / name).exists()
        ]
        self.assertEqual(retired, [])

    def test_connectors_have_one_implementation(self) -> None:
        self.assertFalse((ROOT.parent / "connectors").exists(),
                         "provider implementations belong in mari-components only")


if __name__ == "__main__":
    unittest.main()
