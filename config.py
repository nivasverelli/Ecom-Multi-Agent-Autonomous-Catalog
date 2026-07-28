"""
Bootstrap / configuration for the orchestration framework.

Importing this module (which every other module does, directly or transitively)
performs the one-time bootstrap, in order:
  1. random.seed(42)              -> low-variance reproducibility. MUST run before
                                     any other module consumes the RNG (the
                                     simulator's scheduled-event sampling relies
                                     on this; simulator/market.py imports config
                                     first to guarantee it).
  2. UTF-8 console reconfigure    -> printing LLM text never crashes on Windows.
  3. load_dotenv()                -> read OPENROUTER_API_KEY etc. from .env
  4. model routing constants
  5. key presence check (exit cleanly with setup instructions if missing)
"""

import os
import random
import sys

# --- Principle 3: low-variance reproducibility. Seed BEFORE anything random. ---
random.seed(42)

# LLM responses can contains an ok ok testing is fine arbitrary Unicode (arrows, dashes, ...). Force UTF-8
# console output so printing a model's text never crashes on Windows (cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # older streams / already-wrapped; best effort


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no external dependency).

    Reads simple KEY=VALUE lines from a .env file alongside main.py and sets
    them in os.environ if not already present. Lines starting with '#' and
    blank lines are ignored. Existing environment variables win.
    """
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_dotenv()

# Models (routed through OpenRouter). Specialists are cheap; the manager (the
# P&L owner that resolves trade-offs) gets the stronger model.
SPECIALIST_MODEL = os.environ.get("SPECIALIST_MODEL", "openai/gpt-4o-mini")
# All agents share one model to keep costs low; override MANAGER_MODEL in .env to
# give the P&L-owner manager a stronger (pricier) model if desired.
MANAGER_MODEL = os.environ.get("MANAGER_MODEL", "openai/gpt-4o-mini")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()

if not OPENROUTER_API_KEY:
    print(
        "\n[SETUP REQUIRED] OPENROUTER_API_KEY is not set.\n"
        "  1. Open the .env file next to main.py.\n"
        "  2. Add your key:  OPENROUTER_API_KEY=sk-or-...\n"
        "  3. Re-run:        python main.py\n"
        "Get a key at https://openrouter.ai/keys\n"
    )
    sys.exit(0)
