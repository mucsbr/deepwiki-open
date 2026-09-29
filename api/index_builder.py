"""Bounded embedding inputs, durable partial indexes, and targeted retry.

Only documents/coverage are serialized: provider clients and request payloads
must never become part of an index's transformer state.
"""

from copy import deepcopy
import hashlib
import re

from adalflow.components.data_process import TextSplitter
from adalflow.core.db import LocalDB
import tiktoken

from api.config import configs, get_embedder_config, get_embedder_type
from api.index_state import embedding_mismatch, index_coverage, valid_vector
from api.tools.embedder import get_embedder

DATA_URI = re.compile(r"data:[\w.+/-]+(?:;[\w=.+-]+)*;base64,[A-Za-z0-9+/=\r\n]+", re.I)


class IndexBuildError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(f"No usable embeddings: {report['failed_chunks']}/{report['total_chunks']} chunks failed."
                         if report["total_chunks"] else "No eligible source chunks were found after filtering.")


def safe_failure(error) -> tuple[str, str]:
    """Do not persist raw provider exceptions (they may contain tokens/input)."""
    status = getattr(error, "status_code", None)
    match = re.search(r"(?:Error code:|HTTP(?:/\d\.\d)?)\s*(\d{3})", str(error))
    status = status or (int(match[1]) if match else None)
    if status:
        return f"http_{status}", f"Embedding service returned HTTP {status}."
    if "timeout" in type(error).__name__.lower() or "timed out" in str(error).lower():
        return "timeout", "Embedding service timed out."
    return "embedding_failed", "Embedding service returned no valid vector; retry this chunk."


def chunk_key(doc):
    path = (doc.meta_data or {}).get("file_path", "")
    return hashlib.sha256((path + "\0" + doc.text).encode()).hexdigest()


def prepare_chunks(documents):
    config = get_embedder_config()
    # Conservative bounds, not a claim that every provider uses cl100k tokens.
    max_tokens = max(32, min(int(config.get("max_chunk_tokens", 2048)), 8192))
    max_chars = max(128, min(int(config.get("max_chunk_chars", 6000)), 16000))
    encoder = tiktoken.get_encoding("cl100k_base")
    splitter = TextSplitter(**configs["text_splitter"])
    chunks, removed = [], 0

    def bounded(text):
        if len(text) <= max_chars and len(encoder.encode(text, disallowed_special=())) <= max_tokens:
            return [text] if text.strip() else []
        middle = len(text) // 2
        return bounded(text[:middle]) + bounded(text[middle:])

    for document in documents:
        cleaned, count = DATA_URI.subn("[embedded binary data omitted]", document.text)
        removed += count
        ordinal = 0
        for text in splitter.split_text(cleaned):
            for part in bounded(text):
                doc = deepcopy(document)
                doc.text, doc.vector = part, []
                doc.meta_data = {**(document.meta_data or {}), "chunk_index": ordinal}
                doc.meta_data["chunk_id"] = chunk_key(doc)
                doc.id = f"{doc.meta_data['chunk_id']}-{ordinal}"
                chunks.append(doc)
                ordinal += 1
    return chunks, removed


def build_index(documents, expected, revision, embedder_type=None, previous=None, force=False):
    chunks, removed = prepare_chunks(documents)
    cached = {}
    if previous is not None and revision and getattr(previous, "source_revision", None) == revision and not embedding_mismatch(previous, expected):
        cached = {chunk_key(d): d.vector for d in previous.get_transformed_data(key="split_and_embed") or []
                  if valid_vector(getattr(d, "vector", None), expected.get("dimensions"))}
    dimensions = expected.get("dimensions")
    if dimensions is None and cached:
        dimensions = len(next(iter(cached.values())))
    failures, reused = {}, set()
    for i, doc in enumerate(chunks):
        vector = cached.get(chunk_key(doc))
        if not force and valid_vector(vector, dimensions):
            doc.vector = vector
            reused.add(i)

    pending = [i for i, d in enumerate(chunks) if not valid_vector(d.vector, dimensions)]
    embedder = get_embedder(embedder_type=embedder_type) if pending else None
    single = (embedder_type or get_embedder_type()) == "ollama"
    batch_size = 1 if single else max(1, min(int(get_embedder_config().get("batch_size", 10)), 100))

    def failed(indices, code, message):
        for i in indices:
            failures[i] = {"path": chunks[i].meta_data.get("file_path", ""),
                           "chunk_id": chunk_key(chunks[i]), "chunk_index": chunks[i].meta_data["chunk_index"],
                           "code": code, "message": message}

    def embed(indices):
        nonlocal dimensions
        try:
            inputs = [chunks[i].text for i in indices]
            result = embedder(input=inputs[0] if single else inputs)
            error = getattr(result, "error", None)
            if error:
                code, message = safe_failure(error)
            else:
                data = getattr(result, "data", None) or []
                # Never silently assign a short or reordered response to wrong chunks.
                positions = [getattr(item, "index", None) for item in data]
                if data and all(isinstance(p, int) and 0 <= p < len(indices) for p in positions) and len(set(positions)) == len(positions):
                    pairs = zip(positions, data)
                elif len(data) == len(indices) and all(p is None for p in positions):
                    pairs = enumerate(data)
                else:
                    pairs = []
                for position, item in pairs:
                    vector = getattr(item, "embedding", None)
                    if valid_vector(vector, dimensions):
                        dimensions = dimensions or len(vector)
                        chunks[indices[position]].vector = vector
                missing = [i for i in indices if not valid_vector(chunks[i].vector, dimensions)]
                failed(missing, "invalid_vector", "Embedding response is missing a valid vector of the expected dimension.")
                return
        except Exception as exc:
            code, message = safe_failure(exc)
        # Split only input-specific rejections. Do not amplify outages/rate limits.
        if code in {"http_400", "http_422"} and len(indices) > 1:
            middle = len(indices) // 2
            embed(indices[:middle])
            embed(indices[middle:])
        else:
            failed(indices, code, message)

    for start in range(0, len(pending), batch_size):
        embed(pending[start:start + batch_size])
    # A forced refresh must not destroy a still-compatible successful vector.
    for i in list(failures):
        vector = cached.get(chunk_key(chunks[i]))
        if valid_vector(vector, dimensions):
            chunks[i].vector = vector
            failures.pop(i)
            reused.add(i)
    db = LocalDB(transformed_items={"split_and_embed": chunks})
    db.index_embedding_spec, db.source_revision = expected, revision
    db.index_vector_dimensions = dimensions
    db.index_failures = list(failures.values())
    db.sanitized_data_uris, db.reused_chunks = removed, len(reused)
    report = index_coverage(db, expected)
    if not report["indexed_chunks"]:
        raise IndexBuildError(report)
    return db
