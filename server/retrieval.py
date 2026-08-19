"""MUVERA + PolarQuant retrieval over rebuildable document vectors.

The implementation is adapted from the evaluated pipelines in ``rt-intent``:
multi-vector chunks are encoded into MUVERA fixed-dimensional encodings (FDEs),
documents are compressed to the 0.5-bit block-2 PolarQuant representation, and
the small candidate set is reranked with exact MaxSim over the original chunk
vectors.  Every artifact is derived: it may be deleted and rebuilt at any time.

Artifacts are atomically flushed to ``MARI_VECTOR_URI``.  A plain path stores
them on the local filesystem; ``s3://bucket/prefix`` uses a local read-through
cache and mirrors completed snapshots to S3.  No vector is part of canonical
relational state.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import pathlib
import threading
import time
import typing as t
from collections import defaultdict

import numpy as np


@dataclasses.dataclass(frozen=True)
class FDEConfig:
    repetitions: int = 20
    simhash_bits: int = 5
    projection_dimension: int = 8
    seed: int = 1
    fill_empty_partitions: bool = True

    @property
    def partitions(self) -> int:
        return 1 << self.simhash_bits

    @property
    def dimension(self) -> int:
        return self.repetitions * self.partitions * self.projection_dimension


def projection_parameters(config: FDEConfig, input_dimension: int):
    """Data-oblivious SimHash and CountSketch projections from MUVERA."""
    output = []
    for repetition in range(config.repetitions):
        rng = np.random.default_rng(config.seed + repetition)
        simhash = rng.normal(size=(input_dimension, config.simhash_bits)).astype(np.float32)
        destinations = rng.integers(0, config.projection_dimension, size=input_dimension)
        signs = rng.choice(np.asarray([-1.0, 1.0], np.float32), size=input_dimension)
        projection = np.zeros((input_dimension, config.projection_dimension), np.float32)
        projection[np.arange(input_dimension), destinations] = signs
        output.append((simhash, projection))
    return output


def _gray_partition(bits: np.ndarray) -> np.ndarray:
    index = np.zeros(len(bits), np.int32)
    for column in range(bits.shape[1]):
        index = (index << 1) + np.logical_xor(bits[:, column], index & 1)
    return index


def _partition_bits(partitions: int, width: int) -> np.ndarray:
    gray = np.arange(partitions, dtype=np.int32)
    binary = np.bitwise_xor(gray, gray >> 1)
    shifts = np.arange(width - 1, -1, -1, dtype=np.int32)
    return ((binary[:, None] >> shifts[None, :]) & 1).astype(bool)


def encode_fde(points: np.ndarray, config: FDEConfig, parameters, *, query: bool) -> np.ndarray:
    """Encode query points by sum and document points by partition centroid."""
    points = np.asarray(points, np.float32)
    if points.ndim != 2 or not len(points):
        raise ValueError("MUVERA needs at least one input vector")
    output = np.zeros(
        (config.repetitions, config.partitions, config.projection_dimension), np.float32)
    target_bits = _partition_bits(config.partitions, config.simhash_bits)
    for repetition, (simhash, projection) in enumerate(parameters):
        signs = points @ simhash > 0
        buckets = _gray_partition(signs)
        projected = points @ projection
        np.add.at(output[repetition], buckets, projected)
        if query:
            continue
        counts = np.bincount(buckets, minlength=config.partitions)
        occupied = counts > 0
        output[repetition, occupied] /= counts[occupied, None]
        if config.fill_empty_partitions and np.any(~occupied):
            for bucket in np.flatnonzero(~occupied):
                nearest = int(np.argmin(np.count_nonzero(signs != target_bits[bucket], axis=1)))
                output[repetition, bucket] = projected[nearest]
    return output.reshape(-1)


def _orthogonal_rotation(dimension: int, seed: int = 91) -> np.ndarray:
    rng = np.random.default_rng(seed + dimension)
    q, r = np.linalg.qr(rng.normal(size=(dimension, dimension)))
    q *= np.sign(np.diag(r))[None, :]
    return q.astype(np.float32)


def _fit_two_centers(values: np.ndarray) -> np.ndarray:
    """Deterministic one-dimensional two-means; avoids a runtime sklearn dependency."""
    values = np.asarray(values, np.float32).reshape(-1)
    centers = np.quantile(values, [0.25, 0.75]).astype(np.float32)
    for _ in range(32):
        split = float(np.mean(centers))
        low, high = values[values <= split], values[values > split]
        updated = np.asarray([
            low.mean() if len(low) else centers[0],
            high.mean() if len(high) else centers[1],
        ], np.float32)
        if np.allclose(updated, centers):
            break
        centers = updated
    return np.sort(centers)


def train_polar(fdes: np.ndarray) -> tuple[dict, np.ndarray]:
    """Train and encode the rt-intent block-2, one-angle-bit, zero-radius codec."""
    fdes = np.asarray(fdes, np.float32)
    if fdes.ndim != 2 or fdes.shape[1] % 2:
        raise ValueError("PolarQuant block-2 encoding needs an even FDE dimension")
    rotation = _orthogonal_rotation(2)
    blocks = fdes.reshape(len(fdes), -1, 2) @ rotation
    angles = np.arctan2(blocks[..., 1], blocks[..., 0])
    centers = _fit_two_centers(angles)
    boundary = float(np.mean(centers))
    packed = np.packbits((angles > boundary).astype(np.uint8), axis=1)
    # One shared radius is intentional for the 0.5-bit format. It changes score
    # scale, not order, while keeping the representation at one bit per pair.
    radius = float(np.linalg.norm(blocks, axis=2).mean())
    codec = {
        "name": "polar_ultra_1bit_block2_r0",
        "dimension": int(fdes.shape[1]),
        "angle_centers": [centers.tolist()],
        "radius_centers": [radius],
        "boundary": boundary,
        "packed_bytes": int(packed.shape[1]),
        "bits_per_fde_coordinate": 0.5,
    }
    return codec, packed


def encode_polar(fde: np.ndarray, codec: dict) -> np.ndarray:
    blocks = np.asarray(fde, np.float32).reshape(-1, 2) @ _orthogonal_rotation(2)
    angles = np.arctan2(blocks[:, 1], blocks[:, 0])
    return np.packbits((angles > float(codec["boundary"])).astype(np.uint8))


def _byte_lookup(query_fde: np.ndarray, codec: dict) -> tuple[float, np.ndarray]:
    rotation = _orthogonal_rotation(2)
    centers = np.asarray(codec["angle_centers"][0], np.float32)
    radius = float(codec["radius_centers"][0])
    prototypes = (radius * np.stack([np.cos(centers), np.sin(centers)], axis=1)) @ rotation.T
    query_blocks = np.asarray(query_fde, np.float32).reshape(-1, 2)
    pair_lookup = query_blocks @ prototypes.T
    base = float(np.sum(pair_lookup[:, 0], dtype=np.float32))
    deltas = (pair_lookup[:, 1] - pair_lookup[:, 0]).reshape(-1, 8)
    byte_values = np.arange(256, dtype=np.uint16)
    shifts = np.arange(7, -1, -1, dtype=np.uint16)
    bits = ((byte_values[:, None] >> shifts) & 1).astype(np.float32)
    return base, deltas @ bits.T


def polar_scores(index: np.ndarray, query_fde: np.ndarray, codec: dict) -> np.ndarray:
    base, lookup = _byte_lookup(query_fde, codec)
    scores = np.full(len(index), base, np.float32)
    for position in range(index.shape[1]):
        scores += lookup[position, index[:, position]]
    return scores


def exact_maxsim(query_points: np.ndarray, document_points: np.ndarray) -> float:
    # Copies are deliberate: persisted arrays are memory-mapped read-only and
    # normalization must never mutate either the caller's query or the index.
    qv = np.array(query_points, dtype=np.float32, copy=True)
    dv = np.array(document_points, dtype=np.float32, copy=True)
    qv /= np.maximum(np.linalg.norm(qv, axis=1, keepdims=True), 1e-12)
    dv /= np.maximum(np.linalg.norm(dv, axis=1, keepdims=True), 1e-12)
    return float((qv @ dv.T).max(axis=1).sum())


class DerivedVectorIndex:
    """Atomic, reloadable derived index with optional S3 mirroring."""

    def __init__(self, uri: str | None = None, config: FDEConfig | None = None):
        default_path = pathlib.Path(__file__).resolve().parent.parent / ".mari" / "vectors"
        self.uri = uri or os.environ.get("MARI_VECTOR_URI", str(default_path))
        self.config = config or FDEConfig()
        self._lock = threading.RLock()
        self._loaded_at = 0.0
        self._snapshot: dict[str, t.Any] | None = None
        if self.uri.startswith("s3://"):
            cache = os.environ.get("MARI_VECTOR_CACHE", ".mari/vector-cache")
            self.path = pathlib.Path(cache)
        else:
            self.path = pathlib.Path(self.uri).expanduser()

    def build(self, documents: dict[int, np.ndarray], hashes: dict[int, str] | None = None) -> dict:
        clean = {int(k): np.asarray(v, np.float32) for k, v in documents.items() if len(v)}
        if not clean:
            raise ValueError("cannot build an empty vector index")
        dims = {v.shape[1] for v in clean.values() if v.ndim == 2}
        if len(dims) != 1 or any(v.ndim != 2 for v in clean.values()):
            raise ValueError("all document vector matrices must share one dimension")
        ids = np.asarray(sorted(clean), np.int64)
        parameters = projection_parameters(self.config, next(iter(dims)))
        fdes = np.stack([encode_fde(clean[int(i)], self.config, parameters, query=False)
                         for i in ids]).astype(np.float32)
        codec, packed = train_polar(fdes)
        offsets = np.zeros(len(ids) + 1, np.int64)
        offsets[1:] = np.cumsum([len(clean[int(i)]) for i in ids])
        vectors = np.concatenate([clean[int(i)] for i in ids]).astype(np.float32)
        metadata = {
            "version": 1,
            "built_at": time.time(),
            "documents": len(ids),
            "vectors": len(vectors),
            "input_dimension": int(vectors.shape[1]),
            "fde": dataclasses.asdict(self.config) | {"dimension": self.config.dimension},
            "polar": codec,
            "hashes": {str(i): (hashes or {}).get(int(i), "") for i in ids},
        }
        self._write_snapshot(metadata, ids, offsets, vectors, packed)
        with self._lock:
            self._snapshot = {"metadata": metadata, "ids": ids, "offsets": offsets,
                              "vectors": vectors, "packed": packed}
            self._loaded_at = time.time()
        return metadata

    def search(self, query_points: np.ndarray, k: int = 10, candidate_k: int = 1000) -> list[dict]:
        snap = self._load()
        if not snap:
            return []
        query_points = np.asarray(query_points, np.float32)
        params = projection_parameters(self.config, int(snap["metadata"]["input_dimension"]))
        query_fde = encode_fde(query_points, self.config, params, query=True)
        approx = polar_scores(snap["packed"], query_fde, snap["metadata"]["polar"])
        take = min(max(k, candidate_k), len(approx))
        positions = np.argpartition(-approx, take - 1)[:take] if take < len(approx) else np.arange(len(approx))
        exact = []
        for pos in positions:
            start, stop = int(snap["offsets"][pos]), int(snap["offsets"][pos + 1])
            exact.append(exact_maxsim(query_points, snap["vectors"][start:stop]))
        order = np.argsort(-np.asarray(exact), kind="stable")[:k]
        return [{"document_id": int(snap["ids"][positions[i]]),
                 "score": float(exact[i]), "approx_score": float(approx[positions[i]])}
                for i in order]

    def _write_snapshot(self, metadata: dict, ids: np.ndarray, offsets: np.ndarray,
                        vectors: np.ndarray, packed: np.ndarray) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        files: dict[str, bytes] = {}
        for name, value in (("document_ids.npy", ids), ("offsets.npy", offsets),
                            ("vectors.npy", vectors), ("polar.npy", packed)):
            buf = io.BytesIO()
            np.save(buf, value, allow_pickle=False)
            files[name] = buf.getvalue()
        files["metadata.json"] = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        for name, body in files.items():
            temporary = self.path / f".{name}.tmp"
            temporary.write_bytes(body)
            temporary.replace(self.path / name)
        if self.uri.startswith("s3://"):
            self._mirror_s3(files)

    def _mirror_s3(self, files: dict[str, bytes]) -> None:
        import boto3
        bucket_key = self.uri[5:]
        bucket, _, prefix = bucket_key.partition("/")
        client = boto3.client("s3")
        for name, body in files.items():
            key = "/".join(v for v in (prefix.rstrip("/"), name) if v)
            client.put_object(Bucket=bucket, Key=key, Body=body)

    def _load(self) -> dict[str, t.Any] | None:
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot
            if self.uri.startswith("s3://") and not (self.path / "metadata.json").exists():
                self._pull_s3()
            try:
                metadata = json.loads((self.path / "metadata.json").read_text())
                self._snapshot = {
                    "metadata": metadata,
                    "ids": np.load(self.path / "document_ids.npy", mmap_mode="r"),
                    "offsets": np.load(self.path / "offsets.npy", mmap_mode="r"),
                    "vectors": np.load(self.path / "vectors.npy", mmap_mode="r"),
                    "packed": np.load(self.path / "polar.npy", mmap_mode="r"),
                }
                self._loaded_at = time.time()
            except (OSError, ValueError, json.JSONDecodeError):
                return None
            return self._snapshot

    def _pull_s3(self) -> None:
        import boto3
        bucket_key = self.uri[5:]
        bucket, _, prefix = bucket_key.partition("/")
        self.path.mkdir(parents=True, exist_ok=True)
        client = boto3.client("s3")
        for name in ("metadata.json", "document_ids.npy", "offsets.npy", "vectors.npy", "polar.npy"):
            key = "/".join(v for v in (prefix.rstrip("/"), name) if v)
            try:
                client.download_file(bucket, key, str(self.path / name))
            except Exception:  # noqa: BLE001 -- an absent derived cache is a cache miss
                return

    @property
    def available(self) -> bool:
        return self._load() is not None


_INDEXES: dict[int, DerivedVectorIndex] = {}
_INDEXES_LOCK = threading.Lock()


def _project_uri(base: str, project_id: int) -> str:
    return f"{base.rstrip('/')}/projects/{int(project_id)}"


def index_for(project_id: int) -> DerivedVectorIndex:
    """One derived artifact namespace per project, locally and in S3."""
    project_id = int(project_id)
    with _INDEXES_LOCK:
        if project_id not in _INDEXES:
            default_path = pathlib.Path(__file__).resolve().parent.parent / ".mari" / "vectors"
            base = os.environ.get("MARI_VECTOR_URI", str(default_path))
            _INDEXES[project_id] = DerivedVectorIndex(_project_uri(base, project_id))
        return _INDEXES[project_id]

_REBUILD_LOCK = threading.Lock()
_REBUILD_TIMERS: dict[int, threading.Timer] = {}


def _parse_vector(value: t.Any) -> np.ndarray | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    try:
        vector = np.asarray(value, np.float32)
    except (TypeError, ValueError):
        return None
    return vector if vector.ndim == 1 and len(vector) else None


def rebuild_from_database() -> dict | None:
    """Snapshot canonical chunk vectors. Imported lazily to avoid db cycles."""
    import access
    from db import pq
    context = access.require_current_access()
    rows = pq("""SELECT document_id, content_hash, embedding::text AS embedding
                 FROM chunks WHERE project_id = %s AND embedding IS NOT NULL
                 ORDER BY document_id, idx""")
    grouped: dict[int, list[np.ndarray]] = defaultdict(list)
    hashes: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        vector = _parse_vector(row.get("embedding"))
        if vector is not None:
            grouped[int(row["document_id"])].append(vector)
            hashes[int(row["document_id"])].append(str(row.get("content_hash") or ""))
    if not grouped:
        return None
    matrices = {doc_id: np.stack(vectors) for doc_id, vectors in grouped.items()}
    hash_rows = {doc_id: "|".join(values) for doc_id, values in hashes.items()}
    return index_for(context.project_id).build(matrices, hash_rows)


def ensure_index() -> bool:
    import access
    index = index_for(access.require_current_access().project_id)
    if index.available:
        return True
    with _REBUILD_LOCK:
        if index.available:
            return True
        return rebuild_from_database() is not None


def schedule_rebuild(delay: float | None = None) -> None:
    """Debounce ingestion bursts into one periodic atomic vector flush."""
    import access
    context = access.require_current_access()
    project_id = context.project_id
    seconds = float(delay if delay is not None else os.environ.get("MARI_VECTOR_FLUSH_SECONDS", "30"))
    with _REBUILD_LOCK:
        existing = _REBUILD_TIMERS.get(project_id)
        if existing is not None:
            existing.cancel()

        def run() -> None:
            try:
                with access.use_access(context):
                    rebuild_from_database()
            finally:
                with _REBUILD_LOCK:
                    _REBUILD_TIMERS.pop(project_id, None)

        timer = threading.Timer(max(0.0, seconds), run)
        timer.daemon = True
        _REBUILD_TIMERS[project_id] = timer
        timer.start()
