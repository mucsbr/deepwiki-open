"""Repository-relative exclusions for installed dependencies, not source browsing."""

import hashlib
import json
from pathlib import PurePosixPath


DEPENDENCY_DIRS = frozenset({
    "vendor", "node_modules", "bower_components", "jspm_packages",
    ".pnpm", ".npm", ".venv", "venv", "virtualenv", ".tox", ".nox",
    "__pypackages__", "site-packages", "dist-packages",
    ".m2", ".gradle", ".bundle", "Pods", ".build", "DerivedData",
    ".dart_tool", ".pub-cache", ".next", ".nuxt", ".svelte-kit",
    ".parcel-cache", ".turbo",
})
DEPENDENCY_PATHS = (
    (".yarn", "cache"), (".yarn", "unplugged"), (".yarn", "sdks"),
    (".cargo", "registry"), (".cargo", "git"), (".nuget", "packages"),
    ("Carthage", "Checkouts"), ("Carthage", "Build"),
)
DEPENDENCY_FILTER_SIGNATURE = hashlib.sha256(json.dumps(
    [sorted(DEPENDENCY_DIRS), DEPENDENCY_PATHS], separators=(",", ":")
).encode()).hexdigest()


def directory_matches(relative_path: str, pattern: str) -> bool:
    """Match complete directory components, including configured nested paths."""
    parts = PurePosixPath(relative_path.replace("\\", "/")).parts
    wanted = PurePosixPath(pattern.replace("\\", "/")).parts
    return bool(wanted) and any(
        parts[offset:offset + len(wanted)] == wanted
        for offset in range(len(parts) - len(wanted) + 1)
    )


def is_dependency_path(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path.replace("\\", "/")).parts
    return any(part in DEPENDENCY_DIRS for part in parts) or any(
        parts[offset:offset + len(pattern)] == pattern
        for pattern in DEPENDENCY_PATHS
        for offset in range(len(parts) - len(pattern) + 1)
    )


def dependency_filter_problem(database) -> str | None:
    if getattr(database, "index_dependency_filter_signature", None) != DEPENDENCY_FILTER_SIGNATURE:
        return "Dependency-directory filtering rules changed; rebuild the source index."
    return None
