"""Small, tool-agnostic activity payloads; never publish raw tool output."""

import json
import re


def public_input(value, depth=0):
    if depth >= 4:
        return "…"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if re.search(r"secret|password|token|authorization|cookie|api.?key", str(key), re.I)
            else public_input(item, depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, list):
        return [public_input(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        # Credentials sometimes occur in a URL rather than a separate field.
        value = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", value)
        value = re.sub(r"(?i)([?&](?:token|access_token|api_key|key|password)=)[^&\s]+", r"\1[redacted]", value)
        return value[:1000] + ("…" if len(value) > 1000 else "")
    return value if value is None or isinstance(value, (bool, int, float)) else str(type(value).__name__)


def tool_result(output):
    """Report errors and result sizes without duplicating source or credentials."""
    value = getattr(output, "content", output)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    failed = getattr(output, "status", None) == "error"
    if isinstance(value, dict):
        failed = failed or bool(value.get("error"))
        sizes = {
            key: len(item) for key, item in value.items()
            if isinstance(item, list)
        }
        if isinstance(value.get("content"), str):
            sizes["lines"] = len(value["content"].splitlines())
    else:
        sizes = {"items": len(value)} if isinstance(value, list) else {}
    return {"error": failed, "result": sizes}
