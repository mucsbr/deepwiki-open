"""Shared freshness checks for code embedding caches and batch indexing."""

import subprocess
from pathlib import Path

import numpy as np

def valid_vector(vector, dimensions=None) -> bool:
    try:
        value = np.asarray(vector, dtype=np.float32)
        return bool(value.ndim == 1 and value.size and np.isfinite(value).all()
                    and np.any(value) and (dimensions is None or len(value) == dimensions))
    except (TypeError, ValueError):
        return False


def index_coverage(database, expected=None) -> dict:
    expected = expected or embedding_spec()
    docs = database.get_transformed_data(key="split_and_embed") or []
    dimensions = expected.get("dimensions") or getattr(database, "index_vector_dimensions", None)
    if dimensions is None:
        dimensions = next((len(d.vector) for d in docs if valid_vector(d.vector)), None)
    valid = sum(valid_vector(getattr(d, "vector", None), dimensions) for d in docs)
    failures = getattr(database, "index_failures", [])
    return {
        "total_chunks": len(docs), "indexed_chunks": valid, "failed_chunks": len(docs) - valid,
        "status": "indexed" if docs and valid == len(docs) else "partial" if valid else "error",
        "failures": failures[:100], "failures_truncated": len(failures) > 100,
        "sanitized_data_uris": getattr(database, "sanitized_data_uris", 0),
        "reused_chunks": getattr(database, "reused_chunks", 0),
    }


def embedding_spec(embedder_type: str | None = None) -> dict:
    from api.config import configs, get_embedder_config

    if embedder_type:
        key = (
            f"embedder_{embedder_type}"
            if embedder_type in {"ollama", "google", "bedrock"}
            else "embedder"
        )
        config = configs.get(key, {})
    else:
        config = get_embedder_config()
    kwargs = config.get("model_kwargs", {})
    return {
        "model": kwargs.get("model"),
        "dimensions": kwargs.get("dimensions"),
        "client_class": config.get("client_class"),
        "task_type": kwargs.get("task_type"),
    }


def stored_embedding_specs(database) -> list[dict]:
    recorded = getattr(database, "index_embedding_spec", None)
    if isinstance(recorded, dict) and recorded.get("model"):
        return [recorded]
    specs = []
    for transformer in database.transformer_setups.values():
        for _, component in transformer.named_components():
            kwargs = getattr(component, "model_kwargs", None)
            if not isinstance(kwargs, dict) or not kwargs.get("model"):
                continue
            client = getattr(component, "model_client", None)
            spec = {
                "model": kwargs["model"],
                "dimensions": kwargs.get("dimensions"),
                "client_class": type(client).__name__ if client is not None else None,
                "task_type": kwargs.get("task_type"),
            }
            if spec not in specs:
                specs.append(spec)
    return specs


def embedding_mismatch(database, expected: dict) -> str | None:
    stored = stored_embedding_specs(database)
    if not stored:
        return "The index has no verifiable embedding model metadata."
    for spec in stored:
        if spec.get("model") != expected.get("model"):
            return f"Embedding model changed: {spec.get('model')} -> {expected.get('model')}."
        if spec.get("dimensions") != expected.get("dimensions"):
            return "Embedding dimension configuration changed."
        if spec.get("task_type") != expected.get("task_type"):
            return "Embedding task configuration changed."
        if (
            spec.get("client_class")
            and expected.get("client_class")
            and spec["client_class"] != expected["client_class"]
        ):
            return "Embedding provider type changed."
    return None


def vectors_problem(documents, expected_dimensions=None) -> str | None:
    if not documents:
        return "No source chunks were produced."
    dimensions = set()
    invalid = 0
    for doc in documents:
        try:
            vector = np.asarray(getattr(doc, "vector", None), dtype=np.float32)
            if (
                vector.ndim != 1
                or not vector.size
                or not np.isfinite(vector).all()
                or not np.any(vector)
            ):
                invalid += 1
            else:
                dimensions.add(len(vector))
        except (TypeError, ValueError):
            invalid += 1
    if invalid:
        return f"Missing or invalid embeddings for {invalid}/{len(documents)} source chunks."
    if len(dimensions) != 1:
        return "The index contains mixed embedding dimensions."
    if expected_dimensions is not None and dimensions != {expected_dimensions}:
        return "Returned embedding dimensions do not match the configured dimensions."
    return None


def database_problem(
    database, expected: dict, revision: str | None = None, *, allow_partial: bool = False
) -> str | None:
    mismatch = embedding_mismatch(database, expected)
    if mismatch:
        return mismatch
    if revision is not None and getattr(database, "source_revision", None) != revision:
        return (
            "Indexed source revision is missing or different from the current source."
        )
    if allow_partial and index_coverage(database, expected)["indexed_chunks"]:
        return None
    return vectors_problem(
        database.get_transformed_data(key="split_and_embed"), expected.get("dimensions")
    )


def index_problem(
    path: Path, revision: str | None = None, embedder_type: str | None = None
) -> str | None:
    if not path.is_file() or path.is_symlink():
        return "Index file is missing or is not a regular file."
    try:
        # Register the custom serialized transformer before loading legacy Ollama caches.
        from adalflow.core.db import LocalDB

        import api.ollama_patch  # noqa: F401

        database = LocalDB.load_state(str(path))
        return database_problem(database, embedding_spec(embedder_type), revision)
    except Exception:  # noqa: BLE001 -- incompatible/corrupt pickles may raise SDK-specific errors
        return "Index file is unreadable."


def source_revision(directory: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", directory, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None
