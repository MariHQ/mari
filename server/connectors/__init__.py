"""Mari — connector provider registry.

Every provider is one sibling module exposing PROVIDER (dict), validate(config)
and list_items(config, cursor) per CONNECTORS-CONTRACT.md. This package
discovers them generically (glob the directory — never a hardcoded list) so
modules added in parallel appear automatically.

REGISTRY is a lazily-populated dict: nothing is imported until first access.
Entries:
  REGISTRY[key] = {"key", "provider": PROVIDER, "validate": fn, "list_items": fn}
A module that fails to import (or lacks the contract surface) is skipped with a
warning entry instead of breaking the registry:
  REGISTRY[modname] = {"key": modname, "provider": None, "error": "..."}
Consumers should treat entries with a truthy "error" as unavailable.

Modules whose filename starts with "_" are ignored (helpers, this file).
"""

from __future__ import annotations

import importlib
import pathlib
import sys


def _discover() -> dict:
    out: dict = {}
    pkg_dir = pathlib.Path(__file__).resolve().parent
    for f in sorted(pkg_dir.glob("*.py")):
        name = f.stem
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except Exception as e:  # noqa: BLE001 — a broken sibling must not kill the registry
            msg = f"connector module '{name}' failed to import: {e.__class__.__name__}: {e}"
            print(f"[connectors] WARNING: {msg}", file=sys.stderr)
            out[name] = {"key": name, "provider": None, "error": msg}
            continue
        provider = getattr(mod, "PROVIDER", None)
        validate = getattr(mod, "validate", None)
        list_items = getattr(mod, "list_items", None)
        if not (isinstance(provider, dict) and provider.get("key")
                and callable(validate) and callable(list_items)):
            msg = f"connector module '{name}' does not expose PROVIDER/validate/list_items"
            print(f"[connectors] WARNING: {msg}", file=sys.stderr)
            out[name] = {"key": name, "provider": None, "error": msg}
            continue
        # Provider modules retain their catalog metadata and a temporary
        # direct-call compatibility surface; production registry calls use the
        # infrastructure-neutral mari-components implementations.
        import component_connectors
        validate, list_items = component_connectors.functions(
            str(provider["key"]), validate, list_items)
        out[provider["key"]] = {
            "key": provider["key"],
            "provider": provider,
            "validate": validate,
            "list_items": list_items,
            "module": mod,
        }
    # The shared catalog is authoritative.  Legacy sibling modules may remain
    # temporarily for migration tests, but they neither define availability
    # nor duplicate provider metadata.  This also gives GitHub the same generic
    # worker contract as every other newly connected source.
    from mari_components.connectors import connector_definitions
    import component_connectors

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("legacy connector operation is unavailable")

    for definition in connector_definitions():
        validate, list_items = component_connectors.functions(
            definition.key, unavailable, unavailable,
        )
        out[definition.key] = {
            "key": definition.key,
            "provider": {
                "key": definition.key,
                "name": definition.name,
                "blurb": definition.description,
                "fields": [
                    {
                        "key": field.key,
                        "label": field.label,
                        "secret": field.secret,
                        "required": field.required,
                        "placeholder": field.placeholder,
                        "help": field.help,
                    }
                    for field in definition.fields
                ],
                "docs_url": definition.documentation_url,
            },
            "validate": validate,
            "list_items": list_items,
            "definition": definition,
        }
    return out


class _LazyRegistry(dict):
    """Dict that discovers provider modules on first access."""

    _loaded = False

    def _ensure(self) -> None:
        if not self._loaded:
            self._loaded = True
            super().update(_discover())

    def refresh(self) -> None:
        """Re-scan the package directory (picks up newly added modules)."""
        super().clear()
        self._loaded = True
        super().update(_discover())

    def __getitem__(self, k):
        self._ensure()
        return super().__getitem__(k)

    def __contains__(self, k):
        self._ensure()
        return super().__contains__(k)

    def __iter__(self):
        self._ensure()
        return super().__iter__()

    def __len__(self):
        self._ensure()
        return super().__len__()

    def get(self, k, default=None):
        self._ensure()
        return super().get(k, default)

    def keys(self):
        self._ensure()
        return super().keys()

    def values(self):
        self._ensure()
        return super().values()

    def items(self):
        self._ensure()
        return super().items()


REGISTRY = _LazyRegistry()
