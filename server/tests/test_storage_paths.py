from __future__ import annotations

import pathlib
import unittest

from mari_server import settings as config
from mari_server.providers import vectors as retrieval
from mari_server.persistence.iceberg import warehouse as iceberg_warehouse


class StoragePathTests(unittest.TestCase):
    def test_default_runtime_paths_never_use_dot_mari(self) -> None:
        paths = [
            str(iceberg_warehouse._default_root()),
            str(retrieval.DerivedVectorIndex().path),
        ]
        for path in paths:
            self.assertNotIn(".mari", pathlib.PurePath(path).parts)


if __name__ == "__main__":
    unittest.main()
