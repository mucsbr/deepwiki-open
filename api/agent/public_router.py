"""Anonymous Ask: private visitor histories and administrator-published groups."""

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.public_ask import load_settings, published_repos
from . import router as api
from .access import owner_id
from .models import select_model

router = APIRouter(prefix="/api/public-agent", tags=["public-ask"])


def visitor(key: Annotated[str, Header(alias="X-Public-Ask-Visitor", pattern=r"^[a-f0-9]{64}$")]):
    # The random browser key is a bearer credential, never stored or sent to LLMs.
    return {"public_ask": True, "visitor_owner": hashlib.sha256(key.encode()).hexdigest()}


Visitor = Annotated[dict, Depends(visitor)]


class NewPublicSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[str] = Field(min_length=1, max_length=20)
    system_prompt: str = Field(default="", max_length=16000)
    language: str = Field(default="zh", min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$")


class PublicRun(api.NewRun):
    model_config = ConfigDict(extra="forbid")
    system_prompt: str | None = Field(default=None, max_length=16000)


@router.get("/config")
async def config(request: Request):
    settings = load_settings()
    if not settings.enabled:
        return {"enabled": False, "groups": []}
    rt = api.runtime(request)
    try:
        provider, model = select_model()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "enabled": True,
        "groups": [{"id": group.id, "name": group.name, "description": group.description,
                    "repo_count": len(group.repos)} for group in settings.groups if group.enabled],
        "defaultProvider": provider, "defaultModel": model,
        "maxConcurrentRuns": rt.max_runs,
    }


@router.get("/sessions")
async def list_sessions(request: Request, user: Visitor, offset: int = Query(0, ge=0)):
    result = await api.list_sessions(request, user, offset)
    active = {run["session_id"]: run["status"] for run in api.runtime(request).store.active_for_owner(owner_id(user))}
    result["sessions"] = [{**session, "active_status": active.get(session["id"])} for session in result["sessions"]]
    return result


@router.post("/sessions", status_code=201)
async def create_session(body: NewPublicSession, request: Request, user: Visitor):
    rt = api.runtime(request)
    groups = list(dict.fromkeys(body.groups))
    projects = published_repos(groups)
    if len(projects) > 100:
        raise HTTPException(422, "所选分组合计超过 100 个仓库，请减少分组。")
    scope = await rt.access.create_scope(projects, {**user, "public_groups": groups})
    return rt.store.create_session(owner_id(user), scope, body.language,
                                   public_groups=groups, system_prompt=body.system_prompt)


@router.get("/sessions/{sid}")
async def get_session(sid: UUID, request: Request, user: Visitor):
    return await api.get_session(sid, request, user)


@router.post("/sessions/{sid}/runs", status_code=202)
async def start_run(sid: UUID, body: PublicRun, request: Request, user: Visitor):
    rt = api.runtime(request)
    await api.session_for(rt, str(sid), user)
    existing = rt.store.find_request(str(sid), str(body.request_id))
    if existing:
        return existing
    if body.system_prompt is not None:
        rt.store.set_system_prompt(str(sid), owner_id(user), body.system_prompt)
    # Public users use the operator's agent model, not arbitrary provider names.
    return await api.start_run(sid, api.NewRun(message=body.message, request_id=body.request_id), request, user)


@router.post("/runs/{rid}/resume", status_code=202)
async def resume_run(rid: UUID, request: Request, user: Visitor):
    await api.run_for(api.runtime(request), str(rid), user)
    try:
        provider, model = select_model()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return await api.resume_run(rid, request, user, api.ResumeRun(provider=provider, model=model))


@router.post("/runs/{rid}/cancel")
async def cancel_run(rid: UUID, request: Request, user: Visitor):
    return await api.cancel_run(rid, request, user)


@router.get("/runs/{rid}/events")
async def events(rid: UUID, request: Request, user: Visitor, after: int = Query(0, ge=0)):
    return await api.run_events(rid, request, user, after)
