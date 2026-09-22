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
    remote = git(root, "remote", "get-url", "origin").decode().strip()
    parsed = urlparse(remote)
    # Historic clones may have userinfo. Never expose it or use it in a command.
    clean_remote = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1]).geturl()
    if project_path(clean_remote, gitlab_url) != project:
        raise ValueError(
            "Repository storage name collision; the clone does not match the selected project."
        )
    revision = commit or git(root, "rev-parse", "HEAD").decode().strip()
    if len(revision) not in {40, 64} or any(
        c not in "0123456789abcdef" for c in revision
    ):
        raise ValueError("Invalid source revision.")
    git(root, "cat-file", "-e", f"{revision}^{{commit}}")
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
    """Load existing embedding databases only; never invoke prepare_database."""

    def __init__(self, reader: SourceReader, data_root: Path):
        self.reader, self.data_root = reader, data_root
        self.retrievers = {}

    def search(self, query: str, project: str = "", source: str = "code") -> dict:
        from adalflow.components.retriever.faiss_retriever import FAISSRetriever
        from adalflow.core.db import LocalDB

        from api.config import get_embedder_type
        from api.tools.embedder import get_embedder

        if source not in {"code", "wiki"} or not query.strip() or len(query) > 2000:
            raise ValueError("Use source=code|wiki and a query of 1–2000 characters.")
        projects = [project] if project else list(self.reader.repos)
        for name in projects:
            self.reader.repository(name)
        key = (tuple(projects), source)
        if key not in self.retrievers:
            docs, origins = [], {}
            for name in projects:
                repo = self.reader.repository(name)
                filename = (
                    repo.root.name + ("_wiki" if source == "wiki" else "") + ".pkl"
                )
                path = self.data_root / "databases" / filename
                if not path.is_file() or path.is_symlink():
                    continue
                db = LocalDB.load_state(str(path))
                loaded = db.get_transformed_data(key="split_and_embed") or []
                for doc in loaded:
                    meta = doc.meta_data or {}
                    if source == "code" and meta.get(
                        "file_path"
                    ) not in self.reader.tree(name):
                        continue
                    vector = getattr(doc, "vector", None)
                    if vector is None or len(vector) == 0:
                        continue
                    origins[id(doc)] = name
                    docs.append(doc)
            if not docs:
                return {
                    "matches": [],
                    "notice": "No existing index for this scope/source. Use exact search and read_source.",
                }
            embedder = get_embedder(embedder_type=get_embedder_type())
            query_embedder = (
                (lambda queries: embedder(input=queries[0]))
                if get_embedder_type() == "ollama"
                else embedder
            )
            retriever = FAISSRetriever(
                top_k=min(8, len(docs)),
                embedder=query_embedder,
                documents=docs,
                document_map_func=lambda doc: doc.vector,
            )
            self.retrievers[key] = (retriever, docs, origins)
        retriever, docs, origins = self.retrievers[key]
        results = retriever(query)
        selected = results[0].doc_indices if results else []
        return {
            "matches": [
                {
                    "repo": origins[id(docs[i])],
                    "path": docs[i].meta_data.get("file_path"),
                    "page": docs[i].meta_data.get("page_title"),
                    "text": docs[i].text[:3500],
                }
                for i in selected
            ],
            "notice": "Index/Wiki results are candidates, possibly from a different revision. Confirm with read_source.",
        }
