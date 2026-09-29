"""Single-worker, durable Ask runs; browser disconnects only detach subscribers."""

import asyncio
import fcntl
import logging
import os
import re
import time
from pathlib import Path
from uuid import uuid4

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from fastapi import HTTPException
from langchain.agents.middleware import (
    AgentMiddleware,
    TodoListMiddleware,
)
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from .access import Access
from .activity import public_input, tool_result
from .dsml import recover_dsml, stream_text
from .models import build_model
from .prompts import SYSTEM_PROMPT
from .repositories import SourceReader
from .store import ACTIVE, Store
from .tools import make_tools
from .wiki import WikiReader

logger = logging.getLogger(__name__)


class UnrecoveredDsmlError(RuntimeError):
    """A provider emitted tool markup that could not enter the tool loop."""


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
    """Expose registered tools and recover DSML before the agent decides its next step."""

    def __init__(self, tool_names: set[str]):
        super().__init__()
        self.tool_names = tool_names

    async def awrap_model_call(self, request, handler):
        tools = [
            t
            for t in request.tools
            if getattr(t, "name", "") in self.tool_names
        ]
        response = await handler(request.override(tools=tools))
        messages = [response] if isinstance(response, AIMessage) else response.result
        for message in messages:
            if not isinstance(message, AIMessage) or not isinstance(message.content, str):
                continue
            cleaned, calls = recover_dsml(message.content)
            if cleaned == message.content and not calls:
                continue
            if not calls and not message.tool_calls:
                raise UnrecoveredDsmlError
            message.content = cleaned
            if calls and not message.tool_calls:
                message.tool_calls = [
                    {
                        "name": call.name,
                        "args": call.args,
                        "id": f"dsml_{uuid4().hex}",
                        "type": "tool_call",
                    }
                    for call in calls
                ]
        return response

    async def awrap_tool_call(self, request, handler):
        import json

        call = request.tool_call
        if (
            call["name"] not in self.tool_names
            or len(json.dumps(call.get("args", {}))) > 250_000
        ):
            return ToolMessage(
                content=(
                    "Tool unavailable in this conversation. Use one of the "
                    "registered source tools."
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
                        if event.get("metadata", {}).get("lc_source") == "summarization":
                            continue
                        if kind == "on_chat_model_start":
                            current_model_run, answer, last_emit = event["run_id"], "", 0.0
                        elif (
                            kind == "on_chat_model_stream"
                            and event["run_id"] == current_model_run
                        ):
                            answer += text_content(event["data"]["chunk"].content)
                            draft = stream_text(visible_text(answer))
                            if time.monotonic() - last_emit > 0.15 and draft:
                                self.store.event(
                                    rid, "draft", {"id": current_model_run, "content": draft}
                                )
                                last_emit = time.monotonic()
                        elif kind == "on_chat_model_end" and event["run_id"] == current_model_run:
                            # Flush the tail even when the last tokens arrived inside
                            # the throttle window (or the provider did not stream).
                            output = event["data"].get("output")
                            if output is not None:
                                answer = text_content(output.content)
                            self.store.event(rid, "draft", {
                                "id": current_model_run,
                                "content": stream_text(visible_text(answer)),
                                "done": True,
                            })
                        elif kind == "on_tool_start":
                            self.store.event(
                                rid, "tool_start", {
                                    "id": event["run_id"], "name": name,
                                    "input": public_input(event["data"].get("input", {})),
                                }
                            )
                        elif kind == "on_tool_end":
                            output = event["data"].get("output")
                            self.store.event(
                                rid,
                                "tool_end",
                                {
                                    "id": event["run_id"],
                                    "name": name,
                                    **tool_result(output),
                                },
                            )
                            if name == "write_todos" and not tool_result(output)["error"]:
                                value = event["data"].get("input", {})
                                if isinstance(value, dict):
                                    self.store.event(rid, "plan", {"todos": value.get("todos", [])})
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
            if recover_dsml(answer) != (answer, []):
                raise UnrecoveredDsmlError
            self.store.event(rid, "text", {"id": current_model_run, "content": answer})
            self.store.set_status(rid, "completed", answer=answer)
        except asyncio.CancelledError as exc:
            status = (
                "interrupted" if exc.args and exc.args[0] == "shutdown" else "cancelled"
            )
            self.store.set_status(rid, status, answer=stream_text(visible_text(answer)))
        except TimeoutError:
            self.store.set_status(
                rid,
                "interrupted",
                answer=stream_text(visible_text(answer)),
                error="Run time limit reached. Resume to continue.",
            )
        except HTTPException as exc:
            self.store.set_status(
                rid,
                "failed",
                answer=stream_text(visible_text(answer)),
                error=str(exc.detail),
            )
        except UnrecoveredDsmlError:
            self.store.set_status(
                rid,
                "interrupted",
                answer=stream_text(visible_text(answer)),
                error="The model emitted tool-call markup that could not be recovered. Resume or switch models.",
            )
        except GraphRecursionError:
            self.store.set_status(
                rid,
                "interrupted",
                answer=stream_text(visible_text(answer)),
                error=(
                    "Analysis did not reach a final answer. "
                    "Review the selected repositories, then resume or narrow the question."
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- background runs must persist terminal failure
            logger.warning("Ask run %s failed (%s)", rid, type(exc).__name__)
            self.store.set_status(
                rid,
                "failed",
                answer=stream_text(visible_text(answer)),
                error=f"Analysis failed ({type(exc).__name__}). Check model tool-calling support, provider availability and repository access, then resume.",
            )
        finally:
            # Cancellation/errors can happen inside the stream throttle window.
            # Persist its tail under the same message ID, not as a second answer.
            persisted = self.store.get_run(rid)
            if current_model_run and persisted and persisted["status"] != "completed":
                self.store.event(rid, "draft", {
                    "id": current_model_run,
                    "content": stream_text(visible_text(answer)),
                    "done": True,
                })
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
        reader = SourceReader(repos)
        wiki = WikiReader(reader, self.data_root, session["language"])
        tools = make_tools(reader, self.data_root, self.store, run, authorize, wiki=wiki)
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
                wiki_overview=wiki.prompt_context(),
            ),
            middleware=[
                # Deep Agents needs read_file internally for summarized state;
                # AnalysisBoundary keeps it invisible to the model.
                FilesystemMiddleware(backend=backend, tools=["read_file"]),
                AnalysisBoundary({tool.name for tool in tools} | {"write_todos"}),
                TodoListMiddleware(),
            ],
            checkpointer=self.checkpointer,
            name="deepwiki_ask",
        )
