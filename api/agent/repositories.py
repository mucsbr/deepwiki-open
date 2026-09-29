"""Read-only, revision-pinned source access. No clone, checkout, pull or reindex."""

import fnmatch
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

MAX_FILE_BYTES = 512_000
BLOCKED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "build",
}
BLOCKED_NAMES = {".env", "id_rsa", "id_ed25519", ".npmrc", ".netrc", "credentials.json"}


def allowed_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        bool(parts)
        and not path.startswith("/")
        and ".." not in parts
        and not any(
            p in BLOCKED_DIRS
            or p in BLOCKED_NAMES
            or p.startswith(".env.")
            or p.endswith((".pem", ".key", ".p12", ".pfx"))
            for p in parts
        )
    )


def project_path(value: str, gitlab_url: str) -> str:
    if "://" in value:
        parsed, expected = urlparse(value), urlparse(gitlab_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc != expected.netloc
            or parsed.username
        ):
            raise ValueError(
                "Only repositories on the configured GitLab host are supported."
            )
        value = parsed.path
        base = expected.path.rstrip("/")
        if base:
            if not value.startswith(base + "/"):
                raise ValueError(
                    "Repository is outside the configured GitLab instance."
                )
            value = value[len(base) :]
    value = unquote(value).strip("/").removesuffix(".git")
    if (
        len(value.split("/")) < 2
        or any(p in {"", ".", ".."} for p in value.split("/"))
        or any(c in value for c in "\\\x00\n\r?#")
    ):
        raise ValueError("Invalid GitLab project path.")
    return value


def git(root: Path, *args: str, timeout: int = 15) -> bytes:
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(root), *args],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode not in {0, 1} or (
        result.returncode == 1 and args[0] != "grep"
    ):
        raise ValueError(
            "The pinned repository revision is unavailable; create a new conversation after indexing."
        )
    return result.stdout


@dataclass(frozen=True)
class Repository:
    project: str
    url: str
    commit: str
    root: Path

    def public(self) -> dict:
        return {"project": self.project, "url": self.url, "commit": self.commit}


def resolve_repository(
    project: str, gitlab_url: str, data_root: Path, commit: str | None = None
) -> Repository:
    # Match DatabaseManager's existing naming convention, but verify the remote to
    # prevent collisions such as group_a/repo versus group/a_repo.
    container = (data_root / "repos").resolve()
    root = container / project.replace(".git", "").replace("/", "_")
    if root.is_symlink() or not root.is_dir() or root.resolve().parent != container:
        raise ValueError(f"Source clone for {project} is not available on this server.")
    try:
        remote = git(root, "remote", "get-url", "origin").decode().strip()
    except ValueError as exc:
        raise ValueError(
            f"{project}: local clone has no readable origin remote."
        ) from exc
    parsed = urlparse(remote)
    # Historic clones may have userinfo. Never expose it or use it in a command.
    clean_remote = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1]).geturl()
    try:
        remote_project = project_path(clean_remote, gitlab_url)
    except ValueError as exc:
        raise ValueError(
            f"{project}: local clone origin is not a valid project on this GitLab instance."
        ) from exc
    if remote_project != project:
        raise ValueError(
            f"{project}: repository storage collision; local clone origin "
            "does not match the selected project."
        )
    try:
        revision = commit or git(
            root, "rev-parse", "--verify", "HEAD^{commit}"
        ).decode().strip()
    except ValueError as exc:
        raise ValueError(
            f"{project}: local clone has no readable HEAD commit (empty or incomplete repository)."
        ) from exc
    if len(revision) not in {40, 64} or any(
        c not in "0123456789abcdef" for c in revision
    ):
        raise ValueError(f"{project}: invalid source revision.")
    try:
        git(root, "cat-file", "-e", f"{revision}^{{commit}}")
    except ValueError as exc:
        raise ValueError(
            f"{project}: pinned commit {revision[:12]} is missing from the local clone."
        ) from exc
    return Repository(project, f"{gitlab_url.rstrip('/')}/{project}", revision, root)


class SourceReader:
    def __init__(self, repos: list[Repository]):
        self.repos = {repo.project: repo for repo in repos}
        self.trees: dict[str, dict[str, str]] = {}

    def repository(self, project: str) -> Repository:
        if project not in self.repos:
            raise ValueError(
                "Repository is outside this conversation's authorized scope."
            )
        return self.repos[project]

    def tree(self, project: str) -> dict[str, str]:
        repo = self.repository(project)
        if project not in self.trees:
            files = {}
            for entry in git(repo.root, "ls-tree", "-r", "-z", repo.commit).split(
                b"\0"
            ):
                if not entry:
                    continue
                meta, name = entry.split(b"\t", 1)
                mode, kind, oid = meta.decode().split()
                path = name.decode("utf-8", errors="replace")
                if (
                    mode in {"100644", "100755"}
                    and kind == "blob"
                    and allowed_path(path)
                ):
                    files[path] = oid
            self.trees[project] = files
        return self.trees[project]

    def text(self, project: str, path: str) -> str:
        repo = self.repository(project)
        oid = self.tree(project).get(path)
        if not allowed_path(path) or oid is None:
            raise ValueError(
                "File is not an allowed regular source file in this revision."
            )
        if int(git(repo.root, "cat-file", "-s", oid)) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the source-read size limit.")
        raw = git(repo.root, "cat-file", "blob", oid)
        if b"\0" in raw:
            raise ValueError("Binary files are not supported.")
        return raw.decode("utf-8", errors="replace")

    def list_files(self, project: str, pattern: str = "*", offset: int = 0) -> dict:
        paths = sorted(p for p in self.tree(project) if fnmatch.fnmatchcase(p, pattern))
        offset = max(0, offset)
        return {
            "files": paths[offset : offset + 200],
            "total": len(paths),
            "next_offset": offset + 200 if len(paths) > offset + 200 else None,
        }

    def read(
        self, project: str, path: str, start_line: int = 1, end_line: int = 160
    ) -> dict:
        if start_line < 1 or end_line < start_line:
            raise ValueError("Provide a valid 1-based line range.")
        content = self.text(project, path)
        lines = content.splitlines()
        end_line = min(end_line, start_line + 199, len(lines))
        if start_line > len(lines) and lines:
            raise ValueError("Start line is beyond the end of the file.")
        rendered, used, size = [], start_line - 1, 0
        for number in range(start_line, end_line + 1):
            line = f"{number}: {lines[number - 1]}"
            if size + len(line) > 16000:
                break
            rendered.append(line)
            size += len(line)
            used = number
        repo = self.repository(project)
        return {
            "repo": project,
            "commit": repo.commit,
            "path": path,
            "start_line": start_line,
            "end_line": used,
            "total_lines": len(lines),
            "content": "\n".join(rendered),
            "truncated": used < len(lines),
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "url": f"{repo.url}/-/blob/{repo.commit}/{quote(path, safe='/')}#L{start_line}-{max(start_line, used)}",
        }

    def search(self, query: str, project: str = "", limit: int = 30) -> dict:
        if not query.strip() or len(query) > 300 or "\n" in query:
            raise ValueError(
                "Search must be a nonempty single-line literal of at most 300 characters."
            )
        projects = [project] if project else list(self.repos)
        matches = []
        limit = max(1, min(limit, 50))
        for name in projects:
            repo = self.repository(name)
            candidates = git(
                repo.root,
                "grep",
                "-l",
                "-z",
                "-I",
                "-i",
                "-F",
                "-e",
                query,
                repo.commit,
                "--",
            )
            for item in candidates.split(b"\0"):
                if not item:
                    continue
                path = item.decode(errors="replace").split(":", 1)[-1]
                if path not in self.tree(name):
                    continue
                try:
                    text = self.text(name, path)
                except ValueError:
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if query.casefold() in line.casefold():
                        matches.append(
                            {
                                "repo": name,
                                "commit": repo.commit,
                                "path": path,
                                "line": number,
                                "text": line[:400],
                            }
                        )
                        if len(matches) >= limit:
                            return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}


class IndexSearch:
    """Search existing code embeddings only; never invoke prepare_database."""

    def __init__(self, reader: SourceReader, data_root: Path):
        self.reader, self.data_root = reader, data_root
        self.retrievers = {}

    def search(self, query: str, project: str = "") -> dict:
        from adalflow.components.retriever.faiss_retriever import FAISSRetriever
        from adalflow.core.db import LocalDB

        from api.config import get_embedder_config, get_embedder_type
        from api.index_state import (
            embedding_mismatch,
            embedding_spec,
            stored_embedding_specs,
            index_coverage,
            valid_vector,
        )
        from api.tools.embedder import get_embedder

        if not query.strip() or len(query) > 2000:
            raise ValueError("Use a code-search query of 1–2000 characters.")
        projects = [project] if project else list(self.reader.repos)
        for name in projects:
            self.reader.repository(name)
        config = get_embedder_config()
        expected = embedding_spec()
        model = config.get("model_kwargs", {}).get("model", "")
        key = tuple(projects)
        if key not in self.retrievers:
            docs, origins, coverage = [], {}, []
            for name in projects:
                repo = self.reader.repository(name)
                filename = repo.root.name + ".pkl"
                path = self.data_root / "databases" / filename
                if not path.is_file() or path.is_symlink():
                    coverage.append(
                        {"repo": name, "index_available": False, "indexed_chunks": 0}
                    )
                    continue
                db = LocalDB.load_state(str(path))
                indexed_models = {spec["model"] for spec in stored_embedding_specs(db)}
                mismatch = embedding_mismatch(db, expected)
                revision = getattr(db, "source_revision", None)
                if revision and revision != repo.commit:
                    mismatch = "Indexed source revision differs from this question's source."
                if mismatch:
                    coverage.append({
                        "repo": name,
                        "index_available": True,
                        "indexed_chunks": 0,
                        "indexed_models": sorted(indexed_models),
                        "query_model": model,
                        "reason": f"{mismatch} Rebuild this code index before semantic search.",
                    })
                    continue
                loaded = db.get_transformed_data(key="split_and_embed") or []
                report = index_coverage(db, expected)
                dimensions = expected.get("dimensions") or getattr(db, "index_vector_dimensions", None)
                if dimensions is None:
                    dimensions = next((len(d.vector) for d in loaded if valid_vector(d.vector)), None)
                usable, missing = 0, 0
                for doc in loaded:
                    meta = doc.meta_data or {}
                    if meta.get("file_path") not in self.reader.tree(name):
                        continue
                    vector = getattr(doc, "vector", None)
                    if not valid_vector(vector, dimensions):
                        missing += 1
                        continue
                    origins[id(doc)] = name
                    docs.append(doc)
                    usable += 1
                coverage.append({
                    "repo": name,
                    "index_available": True,
                    "indexed_chunks": usable,
                    "missing_vectors": missing,
                    "total_chunks": report["total_chunks"],
                    "failed_chunks": report["failed_chunks"],
                    "status": report["status"],
                    "failures": report["failures"],
                    "failures_truncated": report["failures_truncated"],
                })
            if not docs:
                return {
                    "matches": [],
                    "index_coverage": coverage,
                    "notice": "No compatible code index for this scope. See index_coverage for model changes or missing indexes. Use Wiki, exact search and read_source until code indexes are rebuilt.",
                }
            embedder_type = get_embedder_type()
            embedder = get_embedder(embedder_type=embedder_type)

            def query_embedder(queries):
                # AdalFlow can return error + empty data instead of raising. Letting
                # that reach FAISS produces an unrelated array-shape ValueError.
                try:
                    instruction = config.get("query_instruction", "")
                    if not instruction and "qwen3-embedding" in model.lower():
                        instruction = "Given a code-search query, retrieve source code snippets that implement the described behavior."
                    inputs = (
                        [f"Instruct: {instruction}\nQuery:{query}" for query in queries]
                        if instruction else queries
                    )
                    result = embedder(inputs[0] if embedder_type == "ollama" else inputs)
                except Exception as exc:
                    raise ValueError(
                        "Code semantic search cannot embed this query because the embedding "
                        "service failed. Use Wiki and exact source search; the configured "
                        "embedding service must be restored."
                    ) from exc
                error = getattr(result, "error", None)
                data = getattr(result, "data", None)
                if error or not data or len(data) != len(queries):
                    reason = (
                        "the embedding gateway has no available channel for the configured model"
                        if "model_not_found" in str(error) or "No available channel" in str(error)
                        else "the embedding service returned no usable query vector"
                    )
                    raise ValueError(
                        f"Code semantic search is unavailable: {reason}. Existing index files "
                        "still require a query embedding. Use Wiki and exact source search; "
                        "do not repeatedly retry this unavailable service."
                    )
                for item in data:
                    vector = getattr(item, "embedding", None)
                    if vector is None or len(vector) != len(docs[0].vector):
                        raise ValueError(
                            "The query embedding dimensions do not match the code index. Restore "
                            "the same embedding model/dimensions used for indexing. Use Wiki and "
                            "exact source search meanwhile."
                        )
                return result

            retriever = FAISSRetriever(
                top_k=min(8, len(docs)),
                embedder=query_embedder,
                documents=docs,
                document_map_func=lambda doc: doc.vector,
            )
            self.retrievers[key] = (retriever, docs, origins, coverage)
        retriever, docs, origins, coverage = self.retrievers[key]
        results = retriever(query)
        selected = results[0].doc_indices if results else []
        return {
            "matches": [
                {
                    "repo": origins[id(docs[i])],
                    "path": docs[i].meta_data.get("file_path"),
                    "text": docs[i].text[:3500],
                }
                for i in selected
            ],
            "search_mode": "semantic_code",
            "index_coverage": coverage,
            "notice": "Code index results are candidates. Confirm with read_source. Check index_coverage: partial indexes omit failed chunks; empty results do not establish absence. Use exact source search for coverage gaps.",
        }
