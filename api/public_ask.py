"""Administrator-owned publication scopes for the login-free Ask page."""

import fcntl
import json
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublicAskGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    repos: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def normalize(self):
        self.name = self.name.strip()
        self.repos = list(dict.fromkeys(self.repos))
        if not self.name or (self.enabled and not self.repos):
            raise ValueError("Enabled groups need a name and at least one repository.")
        return self


class PublicAskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    groups: list[PublicAskGroup] = Field(default_factory=list, max_length=100)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def unique_groups(self):
        if len({group.id for group in self.groups}) != len(self.groups):
            raise ValueError("Group IDs must be unique.")
        if len({group.name.casefold() for group in self.groups}) != len(self.groups):
            raise ValueError("Group names must be unique.")
        if self.enabled and not any(group.enabled for group in self.groups):
            raise ValueError("Select at least one enabled group before publishing Ask.")
        return self


def settings_path() -> Path:
    from adalflow.utils import get_adalflow_default_root_path

    default = Path(get_adalflow_default_root_path()) / "metadata" / "public_ask.json"
    return Path(os.getenv("PUBLIC_ASK_SETTINGS_PATH", str(default)))


def load_settings() -> PublicAskSettings:
    path = settings_path()
    if not path.exists():
        return PublicAskSettings()
    try:
        return PublicAskSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Corrupt settings must never fall back to exposing all indexed repos.
        raise HTTPException(503, "Public Ask settings are unavailable.") from exc


def save_settings(settings: PublicAskSettings) -> PublicAskSettings:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load_settings()
        if settings.revision != current.revision:
            raise HTTPException(409, "Settings changed. Reload before saving.")
        updated = settings.model_copy(update={"revision": current.revision + 1})
        fd, temporary = tempfile.mkstemp(prefix=".public_ask.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(updated.model_dump_json(indent=2))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return updated


def published_repos(group_ids: list[str]) -> list[str]:
    settings = load_settings()
    if not settings.enabled:
        raise HTTPException(403, "免登录 Ask 尚未开放。")
    selected = list(dict.fromkeys(group_ids))
    groups = {group.id: group for group in settings.groups if group.enabled}
    if not selected or any(group not in groups for group in selected):
        raise HTTPException(403, "所选公开分组已关闭或不存在，请重新选择。")
    return list(dict.fromkeys(repo for group in selected for repo in groups[group].repos))


def check_public_scope(projects: list[str], group_ids: list[str]):
    permitted = set(published_repos(group_ids))
    if any(project not in permitted for project in projects):
        raise HTTPException(403, "该会话的仓库开放范围已调整，请创建新会话。")
