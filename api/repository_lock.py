"""One writer per repository, shared by Ask refresh and administrative indexing."""

import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
from pathlib import Path


@asynccontextmanager
async def repository_write_lock(data_root: Path, project: str):
    directory = data_root / "metadata" / "repository-locks"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(project.encode()).hexdigest()
    with (directory / f"{key}.lock").open("a+") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
