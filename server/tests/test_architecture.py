"""Executable dependency rules for the layered Python package."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "mari_server"

DOMAIN_FORBIDDEN = {
    "access", "config", "db", "fastapi", "llm", "psycopg", "strawberry",
    "mari_server.application", "mari_server.infrastructure", "mari_server.api",
}
APPLICATION_FORBIDDEN = {
    "access", "config", "db", "fastapi", "llm", "psycopg", "strawberry",
    "mari_server.infrastructure", "mari_server.api",
}
INFRASTRUCTURE_FORBIDDEN = {"fastapi", "strawberry", "mari_server.api"}


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

    def test_domain_is_pure(self) -> None:
        self.assert_layer("domain", DOMAIN_FORBIDDEN)

    def test_application_depends_only_inward(self) -> None:
        self.assert_layer("application", APPLICATION_FORBIDDEN)

    def test_infrastructure_does_not_own_transports(self) -> None:
        self.assert_layer("infrastructure", INFRASTRUCTURE_FORBIDDEN)

    def test_new_layers_do_not_depend_on_legacy_agent_facade(self) -> None:
        offenders = [
            str(path.relative_to(ROOT)) for path in ROOT.rglob("*.py")
            if "agentchat" in imports(path)
        ]
        self.assertEqual(offenders, [])

    def test_retired_flat_facades_do_not_return(self) -> None:
        server = ROOT.parent
        retired = [
            name for name in ("agentchat.py", "component_connectors.py", "connect_sync.py", "review.py")
            if (server / name).exists()
        ]
        self.assertEqual(retired, [])


if __name__ == "__main__":
    unittest.main()
