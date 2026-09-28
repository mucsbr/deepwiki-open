"""Recover DeepSeek DSML tool calls that an OpenAI-compatible gateway returns as text.

The grammar covers the canonical and doubled-bar forms documented by pi-dsml
(MIT, https://github.com/bestony/pi-dsml). Tool registration and execution stay
with the agent framework; this module only decodes the model's message.
"""

import json
import re
from dataclasses import dataclass

MARKER = r"[｜|]{1,2}\s*DSML\s*[｜|]{1,2}\s*"
OPEN = re.compile(rf"<{MARKER}(?:tool_calls|function_calls|calls)>", re.IGNORECASE)
CLOSE = re.compile(rf"</{MARKER}(?:tool_calls|function_calls|calls)>", re.IGNORECASE)
INVOKE = re.compile(rf"<{MARKER}invoke\s+name=['\"]([^'\"]+)['\"]\s*>", re.IGNORECASE)
INVOKE_END = re.compile(rf"</{MARKER}invoke>", re.IGNORECASE)
PARAMETER = re.compile(
    rf"<{MARKER}parameter\s+name=['\"]([^'\"]+)['\"]"
    rf"(?:\s+string=['\"]?(true|false)['\"]?)?\s*>"
    rf"(.*?)</{MARKER}parameter>",
    re.IGNORECASE | re.DOTALL,
)
STRAY = re.compile(
    rf"</?{MARKER}(?:tool_calls|function_calls|calls|invoke|parameter)"
    r"(?:\s+[^>\n]*)?>?",
    re.IGNORECASE,
)
EOS = re.compile(r"<[｜|]{1,2}\s*end[▁_\s]?of[▁_\s]?sentence\s*[｜|]{1,2}>", re.IGNORECASE)


@dataclass(frozen=True)
class DsmlCall:
    name: str
    args: dict


def _in_fence(text: str, offset: int) -> bool:
    return text[:offset].count("```") % 2 == 1


def recover_dsml(text: str) -> tuple[str, list[DsmlCall]]:
    """Return cleaned assistant text and complete calls, without executing them."""
    if "DSML" not in text.upper():
        return text, []

    calls: list[DsmlCall] = []
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for opening in OPEN.finditer(text):
        if opening.start() < cursor or _in_fence(text, opening.start()):
            continue
        closing = CLOSE.search(text, opening.end())
        end = closing.end() if closing else len(text)
        body_end = closing.start() if closing else end
        body = text[opening.end() : body_end]
        for invocation in INVOKE.finditer(body):
            invocation_end = INVOKE_END.search(body, invocation.end())
            if not invocation_end:
                continue
            args = {}
            for parameter in PARAMETER.finditer(
                body, invocation.end(), invocation_end.start()
            ):
                raw = parameter.group(3).removeprefix("\n").removesuffix("\n")
                if parameter.group(2) == "false":
                    try:
                        value = json.loads(raw.strip())
                    except ValueError:
                        value = raw
                else:
                    value = raw
                args[parameter.group(1)] = value
            calls.append(DsmlCall(invocation.group(1), args))
        ranges.append((opening.start(), end))
        cursor = end

    for pattern in (STRAY, EOS):
        ranges.extend(
            (match.start(), match.end())
            for match in pattern.finditer(text)
            if not _in_fence(text, match.start())
        )
    if not ranges:
        return text, calls
    ranges.sort()
    cleaned = []
    cursor = 0
    for start, end in ranges:
        if start > cursor:
            cleaned.append(text[cursor:start])
        cursor = max(cursor, end)
    cleaned.append(text[cursor:])
    return re.sub(r"\n{3,}", "\n\n", "".join(cleaned)).strip(), calls


def stream_text(text: str) -> str:
    """Hide an unfinished DSML block before it reaches the browser."""
    start = re.search(r"<[｜|]{1,2}\s*D(?:S(?:M(?:L)?)?)?", text, re.IGNORECASE)
    if start and not _in_fence(text, start.start()):
        return text[: start.start()].rstrip()
    return text
