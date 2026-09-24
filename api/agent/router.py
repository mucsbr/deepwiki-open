"""Authenticated Ask API and replayable SSE, without an Agent Server dependency."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .access import current_user, owner_id
from .models import select_model
from .store import ACTIVE, RESUMABLE

router = APIRouter(prefix="/api/agent", tags=["ask-agent"])
AgentUser = Annotated[dict, Depends(current_user)]


@asynccontextmanager
async def agent_lifespan(app):
    app.state.ask_agent = None
    if os.getenv("AGENT_ENABLED", "true").lower() in {"false", "0", "no"}:
        yield
        return
    from adalflow.utils import get_adalflow_default_root_path

    from .runtime import Runtime

    data_root = Path(get_adalflow_default_root_path())
    directory = Path(os.getenv("AGENT_DATA_DIR", str(data_root / "agent")))
    runtime = Runtime(directory, data_root)
    try:
        await runtime.start()
        app.state.ask_agent = runtime
        yield
    finally:
        await runtime.close()


def runtime(request: Request):
    value = getattr(request.app.state, "ask_agent", None)
    if value is None:
        raise HTTPException(
            503,
            "Ask Agent is disabled. Enable AGENT_ENABLED and install the agent dependencies.",
        )
    return value


class NewSession(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=100)
    language: str = Field(
        default="zh", min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$"
    )


class NewRun(BaseModel):
    message: str = Field(min_length=1, max_length=16000)
    request_id: UUID
    provider: str = Field(default="", max_length=40)
    model: str = Field(default="", max_length=200)


class ResumeRun(BaseModel):
    provider: str = Field(default="", max_length=40)
    model: str = Field(default="", max_length=200)


async def session_for(rt, sid: str, user: dict):
    session = rt.store.get_session(sid, owner_id(user))
    if session is None:
        raise HTTPException(404, "Conversation not found")
    await rt.access.check([r["project"] for r in session["repos"]], user)
    return session


async def run_for(rt, rid: str, user: dict):
    run = rt.store.get_run(rid)
    if not run:
        raise HTTPException(404, "Run not found")
    session = await session_for(rt, run["session_id"], user)
    return run, session


@router.get("/config")
async def agent_config(request: Request, user: AgentUser):
    runtime(request)
    try:
        provider, model = select_model()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"defaultProvider": provider, "defaultModel": model}


@router.get("/sessions")
async def list_sessions(
    request: Request, user: AgentUser, offset: int = Query(0, ge=0)
):
    rt = runtime(request)
    sessions = rt.store.list_sessions(owner_id(user), offset=offset)
    visible = []
    for session in sessions:
        try:
            await rt.access.check([r["project"] for r in session["repos"]], user)
            visible.append(session)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
    return {
        "sessions": visible,
        "next_offset": offset + 50 if len(sessions) == 50 else None,
    }


@router.post("/sessions", status_code=201)
async def create_session(body: NewSession, request: Request, user: AgentUser):
    rt = runtime(request)
    scope = await rt.access.create_scope(body.repos, user)
    return rt.store.create_session(owner_id(user), scope, body.language)


@router.get("/sessions/{sid}")
async def get_session(sid: UUID, request: Request, user: AgentUser):
    rt = runtime(request)
    session = await session_for(rt, str(sid), user)
    return {
        **session,
        "runs": rt.store.runs(str(sid)),
        "documents": rt.store.documents(str(sid)),
    }


@router.post("/sessions/{sid}/runs", status_code=202)
async def start_run(sid: UUID, body: NewRun, request: Request, user: AgentUser):
    rt = runtime(request)
    session = await session_for(rt, str(sid), user)
    existing = rt.store.find_request(str(sid), str(body.request_id))
    if existing:
        return existing
    if not body.message.strip():
        raise HTTPException(422, "Message cannot be empty")
    try:
        provider, model = select_model(body.provider, body.model)
        adapter = await asyncio.to_thread(rt.model_factory, provider, model)
        existing = rt.store.find_request(str(sid), str(body.request_id))
        if existing:
            return existing
        rt.available()
        run = rt.store.create_run(
            str(sid), str(body.request_id), body.message.strip(), provider, model
        )
        rt.launch(run, session, None, user, adapter)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return run


@router.post("/runs/{rid}/resume", status_code=202)
async def resume_run(
    rid: UUID, request: Request, user: AgentUser, body: ResumeRun | None = None
):
    rt = runtime(request)
    run, session = await run_for(rt, str(rid), user)
    if run["status"] in ACTIVE:
        return run
    if run["status"] not in RESUMABLE:
        raise HTTPException(409, "Only unfinished runs can be resumed")
    repos = await rt.access.readers(run["repos"], user) if run["repos"] else None
    try:
        provider, model = run["provider"], run["model"]
        if body and (body.provider or body.model):
            provider, model = select_model(
                body.provider or provider, body.model or model
            )
        adapter = await asyncio.to_thread(rt.model_factory, provider, model)
        latest = rt.store.get_run(str(rid))
        if latest["status"] in ACTIVE:
            return latest
        rt.available()
        rt.store.set_model(str(rid), provider, model)
        run = {**run, "provider": provider, "model": model}
        rt.launch(run, session, repos, user, adapter, resume=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return rt.store.get_run(str(rid))


@router.post("/runs/{rid}/cancel")
async def cancel_run(rid: UUID, request: Request, user: AgentUser):
    rt = runtime(request)
    await run_for(rt, str(rid), user)
    await rt.cancel(str(rid))
    return rt.store.get_run(str(rid))


@router.get("/runs/{rid}/events")
async def run_events(
    rid: UUID, request: Request, user: AgentUser, after: int = Query(0, ge=0)
):
    rt = runtime(request)
    _, session = await run_for(rt, str(rid), user)

    async def stream():
        cursor, checks = after, 0
        while not await request.is_disconnected():
            if checks % 80 == 0:
                try:
                    await rt.access.check(
                        [r["project"] for r in session["repos"]], user
                    )
                except HTTPException:
                    return
            events = rt.store.events(str(rid), cursor)
            for event in events:
                cursor = event["id"]
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            run = rt.store.get_run(str(rid))
            if not run or (
                run["status"] not in ACTIVE
                and len(events) < 200
                and not rt.store.events(str(rid), cursor, limit=1)
            ):
                return
            if checks % 40 == 0:
                yield ": keepalive\n\n"
            checks += 1
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
