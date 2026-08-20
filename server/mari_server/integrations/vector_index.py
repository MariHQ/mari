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
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import threading
import time
import typing as t
import uuid
from collections import defaultdict

import numpy as np

from mari_server import config
from mari_components.retrieval import (
    FDEConfig,
    PolarCodec,
    encode_fde,
    exact_maxsim,
    polar_scores,
    projection_parameters,
    train_polar,
)


_ARTIFACTS = ("metadata.json", "document_ids.npy", "offsets.npy", "vectors.npy", "polar.npy")
_GENERATION_RE = re.compile(r"^[a-f0-9]{32}$")


def _s3_client():
    import boto3
    endpoint = config.get("s3", "endpoint_url")
    return boto3.client("s3", **({"endpoint_url": endpoint} if endpoint else {}))


def _file_digest(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _train_for_artifact(fdes: np.ndarray) -> tuple[dict, np.ndarray]:
    codec, packed = train_polar(fdes)
    return {
        "name": codec.name,
        "dimension": codec.dimension,
        "angle_centers": [list(codec.angle_centers)],
        "radius_centers": [codec.radius],
        "boundary": codec.boundary,
        "packed_bytes": codec.packed_bytes,
        "bits_per_fde_coordinate": codec.bits_per_fde_coordinate,
    }, packed


def _component_codec(codec: dict) -> PolarCodec:
    return PolarCodec(
        dimension=int(codec["dimension"]),
        angle_centers=tuple(float(value) for value in codec["angle_centers"][0]),
        radius=float(codec["radius_centers"][0]),
        boundary=float(codec["boundary"]),
        packed_bytes=int(codec["packed_bytes"]),
        name=str(codec.get("name") or "polar_ultra_1bit_block2_r0"),
        bits_per_fde_coordinate=float(codec.get("bits_per_fde_coordinate", 0.5)),
    )


class DerivedVectorIndex:
    """Atomic, reloadable derived index with optional S3 mirroring."""

    def __init__(self, uri: str | None = None, config: FDEConfig | None = None):
        default_path = pathlib.Path(__file__).resolve().parent.parent / "var" / "mari" / "vectors"
        self.uri = uri or os.environ.get("MARI_VECTOR_URI", str(default_path))
        self.config = config or FDEConfig()
        self._lock = threading.RLock()
        self._loaded_at = 0.0
        self._snapshot: dict[str, t.Any] | None = None
        self._snapshot_generation = ""
        self._reload_seconds = max(0.0, float(os.environ.get("MARI_VECTOR_RELOAD_SECONDS", "5")))
        if self.uri.startswith("s3://"):
            cache = os.environ.get("MARI_VECTOR_CACHE", "var/mari/cache/vectors")
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
        codec, packed = _train_for_artifact(fdes)
        offsets = np.zeros(len(ids) + 1, np.int64)
        offsets[1:] = np.cumsum([len(clean[int(i)]) for i in ids])
        vectors = np.concatenate([clean[int(i)] for i in ids]).astype(np.float32)
        metadata = {
            "version": 1,
            "generation": uuid.uuid4().hex,
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
            self._snapshot_generation = metadata["generation"]
            self._loaded_at = time.time()
        return metadata

    def search(self, query_points: np.ndarray, k: int = 10, candidate_k: int = 1000) -> list[dict]:
        snap = self._load()
        if not snap:
            return []
        query_points = np.asarray(query_points, np.float32)
        params = projection_parameters(self.config, int(snap["metadata"]["input_dimension"]))
        query_fde = encode_fde(query_points, self.config, params, query=True)
        approx = polar_scores(snap["packed"], query_fde,
                              _component_codec(snap["metadata"]["polar"]))
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
        generation = str(metadata["generation"])
        generations = self.path / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        staging = generations / f".{generation}.{uuid.uuid4().hex}.tmp"
        staging.mkdir()
        try:
            for name, body in files.items():
                (staging / name).write_bytes(body)
            final = generations / generation
            staging.replace(final)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        manifest = {
            "version": 1,
            "generation": generation,
            "files": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
        }
        pointer = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        temporary = self.path / f".current.{uuid.uuid4().hex}.tmp"
        temporary.write_bytes(pointer)
        temporary.replace(self.path / "current.json")
        if self.uri.startswith("s3://"):
            self._mirror_s3(generation, files, pointer)

    def _mirror_s3(self, generation: str, files: dict[str, bytes], pointer: bytes) -> None:
        bucket_key = self.uri[5:]
        bucket, _, prefix = bucket_key.partition("/")
        client = _s3_client()
        for name, body in files.items():
            key = "/".join(v for v in (prefix.rstrip("/"), "generations", generation, name) if v)
            client.put_object(Bucket=bucket, Key=key, Body=body)
        # The pointer is the commit record and must always be uploaded last.
        key = "/".join(v for v in (prefix.rstrip("/"), "current.json") if v)
        client.put_object(Bucket=bucket, Key=key, Body=pointer)

    def _local_generation(self) -> tuple[str, pathlib.Path] | None:
        pointer = self.path / "current.json"
        try:
            manifest = json.loads(pointer.read_text())
            generation = str(manifest["generation"])
            if not _GENERATION_RE.fullmatch(generation):
                return None
            return generation, self.path / "generations" / generation
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            # Backward-compatible read of pre-generation local snapshots.
            if not pointer.exists() and (self.path / "metadata.json").exists():
                return "legacy", self.path
            return None

    def _load(self) -> dict[str, t.Any] | None:
        with self._lock:
            now = time.time()
            if self._snapshot is not None and now - self._loaded_at < self._reload_seconds:
                return self._snapshot
            if self.uri.startswith("s3://"):
                self._pull_s3()
            local = self._local_generation()
            if local is None:
                return self._snapshot
            generation, directory = local
            if self._snapshot is not None and generation == self._snapshot_generation:
                self._loaded_at = now
                return self._snapshot
            try:
                metadata = json.loads((directory / "metadata.json").read_text())
                if generation != "legacy" and metadata.get("generation") != generation:
                    raise ValueError("metadata generation mismatch")
                candidate = {
                    "metadata": metadata,
                    "ids": np.load(directory / "document_ids.npy", mmap_mode="r"),
                    "offsets": np.load(directory / "offsets.npy", mmap_mode="r"),
                    "vectors": np.load(directory / "vectors.npy", mmap_mode="r"),
                    "packed": np.load(directory / "polar.npy", mmap_mode="r"),
                }
                # Cross-file shape checks turn a corrupt generation into a
                # cache miss while retaining the previously loaded snapshot.
                if len(candidate["offsets"]) != len(candidate["ids"]) + 1:
                    raise ValueError("invalid offset count")
                if len(candidate["packed"]) != len(candidate["ids"]):
                    raise ValueError("invalid packed vector count")
                if int(candidate["offsets"][-1]) != len(candidate["vectors"]):
                    raise ValueError("invalid vector offsets")
            except (OSError, ValueError, json.JSONDecodeError):
                if self.uri.startswith("s3://") and generation != "legacy":
                    # Force the next refresh to redownload this generation;
                    # keep serving the last known-good in-memory snapshot now.
                    (self.path / "current.json").unlink(missing_ok=True)
                return self._snapshot
            self._snapshot = candidate
            self._snapshot_generation = generation
            self._loaded_at = now
            return self._snapshot

    def _pull_s3(self) -> bool:
        bucket_key = self.uri[5:]
        bucket, _, prefix = bucket_key.partition("/")
        self.path.mkdir(parents=True, exist_ok=True)
        generations = self.path / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        client = _s3_client()
        pointer_tmp = self.path / f".remote-current.{uuid.uuid4().hex}.tmp"
        staging: pathlib.Path | None = None
        try:
            pointer_key = "/".join(v for v in (prefix.rstrip("/"), "current.json") if v)
            client.download_file(bucket, pointer_key, str(pointer_tmp))
            pointer_bytes = pointer_tmp.read_bytes()
            manifest = json.loads(pointer_bytes)
            generation = str(manifest["generation"])
            checksums = manifest.get("files") or {}
            if not _GENERATION_RE.fullmatch(generation) or set(checksums) != set(_ARTIFACTS):
                raise ValueError("invalid vector generation manifest")
            local = self._local_generation()
            if (local and local[0] == generation
                    and all((local[1] / name).is_file() for name in _ARTIFACTS)):
                return True
            staging = generations / f".{generation}.{uuid.uuid4().hex}.download"
            staging.mkdir()
            for name in _ARTIFACTS:
                key = "/".join(v for v in (
                    prefix.rstrip("/"), "generations", generation, name) if v)
                downloaded = staging / name
                client.download_file(bucket, key, str(downloaded))
                if _file_digest(downloaded) != checksums[name]:
                    raise ValueError(f"checksum mismatch for {name}")
            final = generations / generation
            if final.exists():
                shutil.rmtree(staging)
            else:
                staging.replace(final)
            local_pointer = self.path / f".current.{uuid.uuid4().hex}.tmp"
            local_pointer.write_bytes(pointer_bytes)
            local_pointer.replace(self.path / "current.json")
            return True
        except Exception:  # noqa: BLE001 -- derived storage failure is a cache miss
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            return False
        finally:
            pointer_tmp.unlink(missing_ok=True)

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
            default_path = pathlib.Path(__file__).resolve().parent.parent / "var" / "mari" / "vectors"
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
    from mari_server.domain import access
    from mari_server.repositories.database import pq
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
    from mari_server.domain import access
    index = index_for(access.require_current_access().project_id)
    if index.available:
        return True
    with _REBUILD_LOCK:
        if index.available:
            return True
        return rebuild_from_database() is not None


def schedule_rebuild(delay: float | None = None) -> None:
    """Debounce ingestion bursts into one periodic atomic vector flush."""
    from mari_server.domain import access
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
