"""Loopback-only UI integration fixture; no real provider, GitLab or user data.

Run: python -m uvicorn tests.agent.preview:app --host 127.0.0.1 --port 8125
Point a local Next.js server's SERVER_BASE_URL at it. The only accepted bearer
token is `agent-ui-fixture`; never mount this app into the production API.
"""

import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from api.agent.router import current_user, router
from api.agent.runtime import Runtime
from tests.agent.test_agent import FakeAccess, TestModel, repository


class PreviewModel(TestModel):
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        await asyncio.sleep(0.3)
        last = messages[-1]
        if last.type == "tool":
            content = (
                "已完成。订单提交逻辑位于 `order.py`，提交后状态变为 `submitted`。"
            )
            if "saved" in last.content:
                content = "流程文档已保存，可以在下方查看和下载。"
            response = AIMessage(content=content)
        elif "文档" in str(last.content):
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_document",
                        "id": "preview-save",
                        "type": "tool_call",
                        "args": {
                            "name": "订单提交流程.md",
                            "content": "# 订单提交流程\n\n提交后状态变为 `submitted`。\n\n```mermaid\ngraph TD\n A[提交] --> B[更新状态]\n```\n\n这是离线交互测试文档。",
                        },
                    }
                ],
            )
        else:
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_source",
                        "id": "preview-read",
                        "type": "tool_call",
                        "args": {"repo": "group/demo", "path": "order.py"},
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


class PreviewAccess(FakeAccess):
    def __init__(self, repo):
        self.repo = repo

    async def create_scope(self, selected, user):
        if not selected or any(
            value not in {"group/demo", self.repo.url} for value in selected
        ):
            raise HTTPException(403, "Only the test fixture is available")
        return [self.repo.public()]

    async def readers(self, scope, user):
        return [self.repo]


@asynccontextmanager
async def lifespan(app):
    directory = Path(tempfile.mkdtemp(prefix="deepwiki-ui-fixture-"))
    _, repo = repository.__wrapped__(directory)
    runtime = Runtime(
        directory / "agent",
        directory,
        access=PreviewAccess(repo),
        model_factory=lambda *_: PreviewModel(responses=[]),
    )
    await runtime.start()
    app.state.ask_agent = runtime
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(lifespan=lifespan)
app.include_router(router)


async def fixture_user(request: Request):
    if request.headers.get("authorization") != "Bearer agent-ui-fixture":
        raise HTTPException(401, "Fixture authentication required")
    return {
        "gitlab_user_id": 1,
        "username": "fixture",
        "name": "Offline fixture",
        "is_admin": False,
    }


app.dependency_overrides[current_user] = fixture_user
app.get("/auth/me")(fixture_user)


@app.get("/api/projects")
def projects():
    return [
        {
            "id": 1,
            "name": "demo",
            "path_with_namespace": "group/demo",
            "description": "Offline fixture",
            "web_url": "https://gitlab.example/group/demo",
            "index_status": "indexed",
            "avatar_url": "",
            "indexed_at": "2026-09-22",
            "last_activity_at": "2026-09-22",
        }
    ]


@app.get("/lang/config")
def languages():
    return {"default": "zh", "supported_languages": {"zh": "中文", "en": "English"}}


@app.get("/models/config")
def models():
    return {
        "defaultProvider": "openai",
        "providers": [
            {
                "id": "openai",
                "name": "Offline fixture",
                "supportsCustomModel": True,
                "models": [{"id": "offline-test", "name": "Offline test"}],
            }
        ],
    }
