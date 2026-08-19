from __future__ import annotations

import tempfile
import unittest

import numpy as np

from retrieval import (
    DerivedVectorIndex, FDEConfig, encode_fde, exact_maxsim,
    polar_scores, projection_parameters, train_polar,
)


class MuveraPolarQuantTests(unittest.TestCase):
    def setUp(self):
        self.cfg = FDEConfig(repetitions=2, simhash_bits=2, projection_dimension=4)

    def test_query_sum_and_document_centroid_are_asymmetric(self):
        points = np.asarray([[1, 0, 0], [1, 0, 0]], np.float32)
        params = projection_parameters(self.cfg, 3)
        query = encode_fde(points, self.cfg, params, query=True)
        document = encode_fde(points, self.cfg, params, query=False)
        self.assertEqual(query.shape, (self.cfg.dimension,))
        self.assertFalse(np.allclose(query, document))

    def test_polarquant_uses_half_bit_per_fde_coordinate(self):
        rng = np.random.default_rng(4)
        fdes = rng.normal(size=(9, self.cfg.dimension)).astype(np.float32)
        codec, packed = train_polar(fdes)
        self.assertEqual(packed.shape, (9, self.cfg.dimension // 16))
        self.assertEqual(codec["bits_per_fde_coordinate"], 0.5)
        scores = polar_scores(packed, fdes[0], codec)
        self.assertEqual(scores.shape, (9,))
        self.assertTrue(np.isfinite(scores).all())

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

    def test_exact_maxsim_rewards_each_query_vector(self):
        query = np.asarray([[1, 0], [0, 1]], np.float32)
        both = np.asarray([[1, 0], [0, 1]], np.float32)
        one = np.asarray([[1, 0]], np.float32)
        self.assertGreater(exact_maxsim(query, both), exact_maxsim(query, one))


if __name__ == "__main__":
    unittest.main()
