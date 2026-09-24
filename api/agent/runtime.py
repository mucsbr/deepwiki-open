"""Single-worker, durable Ask runs; browser disconnects only detach subscribers."""

import asyncio
import fcntl
import logging
import os
import re
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from fastapi import HTTPException
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    TodoListMiddleware,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from .access import Access
from .models import build_model
from .prompts import SYSTEM_PROMPT
from .repositories import SourceReader
from .store import ACTIVE, Store
from .tools import make_tools

logger = logging.getLogger(__name__)

SOURCE_TOOLS = frozenset(
    {
        "list_repositories",
        "list_source_files",
        "search_source",
        "read_source",
        "search_index",
        "load_flow_guide",
        "save_document",
        "write_todos",
    }
)
RESEARCH_MODEL_CALLS = 18
FINALIZE_PROMPT = (
    "The investigation budget is exhausted. Answer now from the source evidence "
    "already collected. Cite verified source lines, clearly identify unresolved "
    "steps, and say when a required backend repository is outside the selected "
    "scope. Do not call any more tools or claim an end-to-end flow without evidence."
)


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def visible_text(text: str) -> str:
    """Some compatible providers put reasoning tags in their text channel."""
    text = re.sub(
        r"<think>.*?(?:</think>|$)", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    for length in range(1, len("<think>")):
        if text.lower().endswith("<think>"[:length]):
            return text[:-length]
    return text


class AnalysisBoundary(AgentMiddleware):
    """Expose only source tools and require a final answer after bounded research."""

    def __init__(self):
        super().__init__()
        self.model_calls = 0

    async def awrap_model_call(self, request, handler):
        self.model_calls += 1
        tools = [
            t
            for t in request.tools
            if getattr(t, "name", "") in SOURCE_TOOLS
        ]
        if self.model_calls > RESEARCH_MODEL_CALLS:
            prompt = request.system_message.text if request.system_message else ""
            return await handler(
                request.override(
                    tools=[],
                    tool_choice=None,
                    system_message=SystemMessage(
                        content=f"{prompt}\n\n{FINALIZE_PROMPT}"
                    ),
                )
            )
        return await handler(request.override(tools=tools))

    async def awrap_tool_call(self, request, handler):
        import json

        call = request.tool_call
        if (
            call["name"] not in SOURCE_TOOLS
            or len(json.dumps(call.get("args", {}))) > 250_000
        ):
            return ToolMessage(
                content=(
                    "Tool unavailable for source analysis. Use search_source, "
                    "read_source or search_index."
                ),
                tool_call_id=call["id"],
                status="error",
            )
        return await handler(request)


class Runtime:
    def __init__(
        self,
        directory: Path,
        data_root: Path,
        *,
        access=None,
        model_factory=build_model,
    ):
        self.directory, self.data_root = directory, data_root
        self.store = Store(directory / "sessions.sqlite3")
        self.access = access or Access(data_root)
        self.model_factory = model_factory
        self.tasks: dict[str, asyncio.Task] = {}
        self.checkpointer = None
        self._checkpoint_context = None
        self._lock_file = None
        self.max_runs = max(1, int(os.getenv("AGENT_MAX_CONCURRENT_RUNS", "4")))
        self.timeout = max(30, int(os.getenv("AGENT_RUN_TIMEOUT_SECONDS", "900")))

    async def start(self):
        self._lock_file = (self.directory / "worker.lock").open("a+")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError(
                "Ask Agent requires one worker per data directory. Run the API with --workers 1."
            ) from exc
        self._checkpoint_context = AsyncSqliteSaver.from_conn_string(
            str(self.directory / "checkpoints.sqlite3")
        )
        self.checkpointer = await self._checkpoint_context.__aenter__()
        await self.checkpointer.setup()
        self.store.recover()

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel("shutdown")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._lock_file:
            self.store.recover()
        if self._checkpoint_context:
            await self._checkpoint_context.__aexit__(None, None, None)
        if self._lock_file:
            self._lock_file.close()
            self._lock_file = None

    def available(self):
        if len(self.tasks) >= self.max_runs:
            raise ValueError("Agent is busy. Try again when another run finishes.")

    def launch(
        self,
        run: dict,
        session: dict,
        repos: list,
        user: dict,
        model,
        *,
        resume: bool = False,
    ):
        self.available()
        if run["id"] in self.tasks:
            raise ValueError("This run is already active.")
        self.store.set_status(run["id"], "queued")
        self.tasks[run["id"]] = asyncio.create_task(
            self.execute(run, session, repos, user, model, resume=resume),
            name=f"ask-{run['id']}",
        )

    async def cancel(self, rid: str):
        task = self.tasks.get(rid)
        if task:
            task.cancel("user")
            await asyncio.gather(task, return_exceptions=True)
        # A task cancelled before its coroutine starts has no finally handler.
        run = self.store.get_run(rid)
        if run and run["status"] in ACTIVE:
            self.store.set_status(rid, "cancelled")
        self.tasks.pop(rid, None)

    async def execute(
        self, run: dict, session: dict, repos: list, user: dict, model, *, resume: bool
    ):
        rid = run["id"]
        answer, last_emit, current_model_run = "", 0.0, None
        try:
            self.store.set_status(rid, "running")
            if repos is None:
                async with asyncio.timeout(self.timeout):
                    repos = await self.access.refresh_readers(
                        session["repos"],
                        user,
                        on_progress=lambda name, status: self.store.event(
                            rid, "refresh", {"repo": name, "phase": status}
                        ),
                    )
            if not run.get("repos"):
                scope = [repo.public() for repo in repos]
                self.store.set_run_repos(rid, scope)
                self.store.event(rid, "source_scope", {"repos": scope})
                session = {**session, "repos": scope}
            graph = self.create_graph(run, session, repos, user, model)
            config = {
                "configurable": {"thread_id": session["id"]},
                "recursion_limit": 200,
            }
            snapshot = await graph.aget_state(config)
            already_submitted = any(
                getattr(m, "id", None) == rid
                for m in snapshot.values.get("messages", [])
            )
            payload = (
                None
                if resume and already_submitted
                else {"messages": [HumanMessage(content=run["message"], id=rid)]}
            )
            # A crash may occur after graph completion but before app status is
            # committed. Reconcile that checkpoint instead of replaying the turn.
            checkpoint_finished = resume and already_submitted and not snapshot.next
            if checkpoint_finished:
                previous = snapshot.values.get("messages", [])[-1]
                if (
                    isinstance(previous, AIMessage)
                    and not visible_text(text_content(previous.content)).strip()
                ):
                    # Retry an empty provider reply without duplicating the user turn.
                    payload = {"messages": [RemoveMessage(id=previous.id)]}
                    checkpoint_finished = False
            if not checkpoint_finished:
                async with asyncio.timeout(self.timeout):
                    async for event in graph.astream_events(
                        payload, config=config, version="v2"
                    ):
                        kind, name = event["event"], event.get("name", "")
                        if kind == "on_chat_model_start":
                            current_model_run, answer = event["run_id"], ""
                        elif (
                            kind == "on_chat_model_stream"
                            and event["run_id"] == current_model_run
                        ):
                            answer += text_content(event["data"]["chunk"].content)
                            if time.monotonic() - last_emit > 0.15 and answer:
                                self.store.event(
                                    rid, "text", {"content": visible_text(answer)}
                                )
                                last_emit = time.monotonic()
                        elif kind == "on_tool_start":
                            self.store.event(
                                rid, "tool_start", {"id": event["run_id"], "name": name}
                            )
                            if name == "write_todos":
                                value = event["data"].get("input", {})
                                if isinstance(value, dict):
                                    self.store.event(
                                        rid, "plan", {"todos": value.get("todos", [])}
                                    )
                        elif kind == "on_tool_end":
                            output = event["data"].get("output")
                            self.store.event(
                                rid,
                                "tool_end",
                                {
                                    "id": event["run_id"],
                                    "name": name,
                                    "error": getattr(output, "status", None) == "error",
                                },
                            )
            snapshot = await graph.aget_state(config)
            last = next(
                (
                    m
                    for m in reversed(snapshot.values.get("messages", []))
                    if isinstance(m, AIMessage)
                ),
                None,
            )
            if last is None or last.tool_calls:
                raise RuntimeError("Agent did not produce a final response.")
            answer = visible_text(text_content(last.content))
            if not answer.strip():
                raise RuntimeError("Model returned an empty final answer.")
            self.store.event(rid, "text", {"content": answer})
            self.store.set_status(rid, "completed", answer=answer)
        except asyncio.CancelledError as exc:
            status = (
                "interrupted" if exc.args and exc.args[0] == "shutdown" else "cancelled"
            )
            self.store.set_status(rid, status, answer=visible_text(answer))
        except TimeoutError:
            self.store.set_status(
                rid,
                "interrupted",
                answer=visible_text(answer),
                error="Run time limit reached. Resume to continue.",
            )
        except HTTPException as exc:
            self.store.set_status(
                rid,
                "failed",
                answer=visible_text(answer),
                error=str(exc.detail),
            )
        except (GraphRecursionError, ModelCallLimitExceededError):
            self.store.set_status(
                rid,
                "interrupted",
                answer=visible_text(answer),
                error=(
                    "Analysis kept requesting tools without a final answer. "
                    "Review the selected repositories, then resume or narrow the question."
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- background runs must persist terminal failure
            logger.warning("Ask run %s failed (%s)", rid, type(exc).__name__)
            self.store.set_status(
                rid,
                "failed",
                answer=visible_text(answer),
                error=f"Analysis failed ({type(exc).__name__}). Check model tool-calling support, provider availability and repository access, then resume.",
            )
        finally:
            self.tasks.pop(rid, None)

    def create_graph(self, run, session, repos, user, model):
        async def authorize():
            await self.access.check([r.project for r in repos], user)

        # OpenAI-compatible custom model names often have no known profile.
        # Avoid the SDK's much larger fallback summarization threshold.
        budget = max(2048, int(os.getenv("AGENT_CONTEXT_TOKENS", "32000")))
        profile = dict(model.profile or {})
        known_limit = profile.get("max_input_tokens")
        profile["max_input_tokens"] = (
            min(budget, known_limit) if isinstance(known_limit, int) else budget
        )
        model.profile = profile
        tools = make_tools(
            SourceReader(repos), self.data_root, self.store, run, authorize
        )
        backend = StateBackend()
        return create_deep_agent(
            model=model,
            tools=tools,
            backend=backend,
            system_prompt=SYSTEM_PROMPT.format(
                language=session["language"],
                revisions="\n".join(
                    f"- {repo.project} @ {repo.commit}" for repo in repos
                ),
            ),
            middleware=[
                # Deep Agents needs read_file internally for summarized state;
                # AnalysisBoundary keeps it invisible to the model.
                FilesystemMiddleware(backend=backend, tools=["read_file"]),
                AnalysisBoundary(),
                TodoListMiddleware(),
                ModelCallLimitMiddleware(run_limit=24, exit_behavior="error"),
            ],
            checkpointer=self.checkpointer,
            name="deepwiki_ask",
        )
