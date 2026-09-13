"""Process environment, read once. Owner: B.

Everything here comes from `.env` (or real environment variables, which win).
Kept apart from `engine/config.py` on purpose: that file is the *simulation's*
parameters — tariffs, limits, the seed — and belongs in version control as
`config.yaml`. This file is *deployment* — API keys, ports, CORS origins — and
must never be committed.

`python-dotenv` is optional. Without it the engine still runs; it just reads the
real environment only, and a `.env` sitting next to the code is ignored rather
than crashing anything.
"""
from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    # `override=False`: a real environment variable beats the file. That is what
    # makes the same code work on Render, where there is no .env at all and the
    # dashboard supplies the values.
    load_dotenv(_REPO_ROOT / ".env", override=False)
    DOTENV_AVAILABLE = True
except ImportError:                                     # pragma: no cover
    DOTENV_AVAILABLE = False


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name) or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(float(_str(name) or default))
    except ValueError:
        return default


# ------------------------------------------------------------------ LLM

GROQ_API_KEY = _str("GROQ_API_KEY")
GROQ_MODEL = _str("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_FALLBACK_MODEL = _str("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")
GROQ_BASE_URL = _str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_TIMEOUT_SECONDS = _float("GROQ_TIMEOUT_SECONDS", 6.0)

#: Two conditions, both required. Asking for the LLM without a key is a
#: misconfiguration that should read as "off", not as a stream of failed calls:
#: every call site falls back anyway, so a keyless "enabled" would just be a slow
#: way to reach the same answer.
LLM_ENABLED = _bool("URJASETU_LLM_ENABLED", False) and bool(GROQ_API_KEY)


def llm_status() -> dict:
    """Why the LLM layer is or is not on. Surfaced through the API so the UI can
    say "deterministic defaults" rather than leaving it ambiguous."""
    if LLM_ENABLED:
        return {"enabled": True, "provider": "groq", "model": GROQ_MODEL,
                "fallback_model": GROQ_FALLBACK_MODEL,
                "reason": "configured"}
    if not _bool("URJASETU_LLM_ENABLED", False):
        return {"enabled": False, "provider": "groq", "model": GROQ_MODEL,
                "fallback_model": GROQ_FALLBACK_MODEL,
                "reason": "URJASETU_LLM_ENABLED is not true"}
    return {"enabled": False, "provider": "groq", "model": GROQ_MODEL,
            "fallback_model": GROQ_FALLBACK_MODEL,
            "reason": "GROQ_API_KEY is empty"}


# --------------------------------------------------------------- server

PORT = _int("PORT", 8000)
CORS_ORIGINS = [o.strip() for o in _str("CORS_ORIGINS", "*").split(",") if o.strip()]
DAYS = _int("URJASETU_DAYS", 30)
STREAM_CADENCE_S = _float("URJASETU_STREAM_CADENCE_S", 3.5)
DERATE = _float("URJASETU_DERATE", 1.0)
