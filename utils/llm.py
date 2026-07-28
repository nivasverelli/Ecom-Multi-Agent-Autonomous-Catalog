"""OpenRouter LLM transport. Constructs the client and wraps the call.

call_llm raises on any transport error so callers fall back deterministically.
"""

import json
from typing import Any, Dict

import config

# Construct the OpenAI-compatible client pointed at OpenRouter. Construction does
# not validate the key; bad keys fail at call time -> deterministic fallback.
try:
    from openai import OpenAI

    _CLIENT = OpenAI(base_url="https://openrouter.ai/api/v1",
                     api_key=config.OPENROUTER_API_KEY)
except Exception as exc:  # pragma: no cover - import/construction guard
    print(f"[WARN] Could not initialise OpenAI client ({exc}). "
          "Run will proceed entirely on deterministic fallbacks.")
    _CLIENT = None


def call_llm(model: str, system_prompt: str, user_payload: Dict[str, Any]) -> str:
    """Wrap the OpenRouter call. temperature=0. Returns raw text.

    Raises on any transport/client error so callers fall back deterministically.
    NOTE: user_payload carries PRE-COMPUTED numbers only - no arithmetic is ever
    requested of the model.
    """
    if _CLIENT is None:
        raise RuntimeError("LLM client unavailable")
    response = _CLIENT.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, default=str)},
        ],
    )
    return response.choices[0].message.content
