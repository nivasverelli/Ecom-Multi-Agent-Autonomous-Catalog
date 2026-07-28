"""JSON parsing for agent responses (fences stripped; loud on failure)."""

import json
import re
from typing import Any, Dict


class AgentParseError(Exception):
    """Raised when an agent's text cannot be parsed into JSON -> fallback."""


def safe_json_parse(raw_text: str) -> Dict[str, Any]:
    """Strip ```json fences / whitespace and json.loads. Raise on failure."""
    if raw_text is None:
        raise AgentParseError("empty response")
    cleaned = raw_text.strip()
    # remove ```json ... ``` or ``` ... ``` fences
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    # if extra prose surrounds the object, grab the outermost { ... }
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            cleaned = match.group(0)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AgentParseError(f"could not parse JSON: {exc}") from exc
