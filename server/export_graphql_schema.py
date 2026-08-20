"""Export or verify the committed GraphQL contract.

The browser adapters are hand-written, so an accidental resolver rename can
otherwise compile on both sides and fail only after deployment. CI compares the
schema Strawberry actually builds with the reviewed snapshot in this repo.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCHEMA_PATH = Path(__file__).with_name("graphql-schema.graphql")


def rendered_schema() -> str:
    from app import schema
    return str(schema).rstrip() + "\n"


def check(path: Path = SCHEMA_PATH) -> bool:
    expected = path.read_text(encoding="utf-8") if path.exists() else ""
    return expected == rendered_schema()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="accept the current schema")
    args = parser.parse_args(argv)
    actual = rendered_schema()
    if args.write:
        SCHEMA_PATH.write_text(actual, encoding="utf-8")
        print(f"Wrote {SCHEMA_PATH}")
        return 0
    if not SCHEMA_PATH.exists():
        print(f"Missing {SCHEMA_PATH}; run python -m export_graphql_schema --write", file=sys.stderr)
        return 1
    if SCHEMA_PATH.read_text(encoding="utf-8") != actual:
        print("GraphQL schema drifted; review it and run python -m export_graphql_schema --write", file=sys.stderr)
        return 1
    print("GraphQL schema matches the committed contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
