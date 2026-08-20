from __future__ import annotations

import pathlib
import unittest

import config
import retrieval
from mari_server.infrastructure import iceberg_warehouse


class StoragePathTests(unittest.TestCase):
    def test_default_runtime_paths_never_use_dot_mari(self) -> None:
        paths = [
            str(config._DEFAULTS["sentence_transformers"]["cache_dir"]),
            str(iceberg_warehouse._default_root()),
            str(retrieval.DerivedVectorIndex().path),
        ]
        for path in paths:
            self.assertNotIn(".mari", pathlib.PurePath(path).parts)


if __name__ == "__main__":
    unittest.main()
