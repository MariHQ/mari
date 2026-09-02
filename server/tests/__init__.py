"""Server workflow and integration contract tests."""

import os

# Unit tests must never reach a database. The settings default is the host's
# own Postgres, and a test that forgets to patch the pool used to run its
# UPDATE there. An unreachable URL turns that into a loud connection error.
os.environ.setdefault("MARI_DB", "postgresql://nobody@127.0.0.1:1/unit_tests_must_not_connect")

