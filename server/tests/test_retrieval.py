from __future__ import annotations

import tempfile
import unittest

import numpy as np

from retrieval import DerivedVectorIndex
from mari_components.retrieval import FDEConfig


class DerivedRetrievalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = FDEConfig(repetitions=2, simhash_bits=2, projection_dimension=4)

    def test_persisted_index_reloads_and_exactly_reranks(self):
        docs = {
            10: np.asarray([[1, 0, 0], [0.9, 0.1, 0]], np.float32),
            20: np.asarray([[0, 1, 0], [0.1, 0.9, 0]], np.float32),
            30: np.asarray([[0, 0, 1]], np.float32),
        }
        with tempfile.TemporaryDirectory() as directory:
            index = DerivedVectorIndex(directory, self.cfg)
            meta = index.build(docs, {10: "a", 20: "b", 30: "c"})
            self.assertEqual(meta["documents"], 3)
            self.assertEqual(meta["polar"]["packed_bytes"], self.cfg.dimension // 16)
            reloaded = DerivedVectorIndex(directory, self.cfg)
            hits = reloaded.search(np.asarray([[0.95, 0.05, 0]], np.float32), k=2)
            self.assertEqual(hits[0]["document_id"], 10)
            self.assertGreater(hits[0]["score"], hits[1]["score"])

if __name__ == "__main__":
    unittest.main()
