"""Process environment, read once. Owner: B.

Everything here comes from `.env` (or real environment variables, which win).
Kept apart from `engine/config.py` on purpose: that file is the *simulation's*
parameters — tariffs, limits, the seed — and belongs in version control as
`config.yaml`. This file is *deployment* — API keys, ports, CORS origins — and
must never be committed.

`python-dotenv` is optional. Without it the engine still runs; it just reads the
real environment only, and a `.env` sitting next to the code is ignored rather
than crashing anything.

IBM integrations added:
  - Sub-Task 1: watsonx.ai (LLM_PROVIDER, WATSONX_* vars, IAM token exchange)
  - Sub-Task 3: IBM App Configuration (APPCONFIGURATION_* vars, live feature flags)
  - Sub-Task 5: IBM Cloud Code Engine (IBM_CODE_ENGINE flag suppresses keep-alive)
"""
from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    # `override=False`: a real environment variable beats the file. That is what
    # makes the same code work on Render/Code Engine, where there is no .env at
    # all and the dashboard supplies the values.
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


# ------------------------------------------------------------------ LLM provider

#: "watsonx" (IBM, default) or "groq" (legacy).
LLM_PROVIDER = _str("LLM_PROVIDER", "watsonx").lower()

# ------------------------------------------------------------------ IBM watsonx.ai  (Sub-Task 1)

WATSONX_API_KEY      = _str("WATSONX_API_KEY")
WATSONX_PROJECT_ID   = _str("WATSONX_PROJECT_ID")
WATSONX_MODEL_ID     = _str("WATSONX_MODEL_ID", "ibm/granite-3-3-8b-instruct")
WATSONX_URL          = _str("WATSONX_URL", "https://us-south.ml.cloud.ibm.com").rstrip("/")

# Cached IAM bearer token for watsonx.ai. Refreshed lazily by _watsonx_token().
_watsonx_token_cache: str = ""
_watsonx_token_expiry: float = 0.0


def _watsonx_token() -> str:
    """Exchange the IBM Cloud IAM API key for a short-lived bearer token.

    Tokens are valid for ~60 minutes. We cache the current one and refresh it
    once it is within 5 minutes of expiry, so the first call after a cold start
    pays one extra HTTP round-trip but subsequent calls are free.

    Raises on any failure — by design, so the caller reaches the same fallback
    path a genuine timeout would, with no second untested code path.
    """
    import time
    import httpx

    global _watsonx_token_cache, _watsonx_token_expiry
    # Reuse the cached token if it is still valid with a 5-minute buffer.
    if _watsonx_token_cache and time.time() < _watsonx_token_expiry - 300:
        return _watsonx_token_cache

    resp = httpx.post(
        "https://iam.cloud.ibm.com/identity/token",
        data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey",
              "apikey": WATSONX_API_KEY},
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        timeout=10.0,
    )
    resp.raise_for_status()
    body = resp.json()
    _watsonx_token_cache = body["access_token"]
    _watsonx_token_expiry = time.time() + float(body.get("expires_in", 3600))
    return _watsonx_token_cache


# ------------------------------------------------------------------ Groq (legacy, LLM_PROVIDER=groq)

GROQ_API_KEY         = _str("GROQ_API_KEY")
GROQ_MODEL           = _str("GROQ_MODEL", "qwen/qwen3.8-27b")
GROQ_FALLBACK_MODEL  = _str("GROQ_FALLBACK_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL        = _str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_TIMEOUT_SECONDS = _float("GROQ_TIMEOUT_SECONDS", 6.0)

# ------------------------------------------------------------------ LLM enabled gate

def _llm_enabled() -> bool:
    """True only when the feature flag is on AND the active provider has a key."""
    if not _bool("URJASETU_LLM_ENABLED", False):
        return False
    if LLM_PROVIDER == "groq":
        return bool(GROQ_API_KEY)
    # watsonx (default) and any future provider
    return bool(WATSONX_API_KEY) and bool(WATSONX_PROJECT_ID)


LLM_ENABLED = _llm_enabled()


def llm_status() -> dict:
    """Why the LLM layer is or is not on. Surfaced through /api/health."""
    if LLM_ENABLED:
        if LLM_PROVIDER == "groq":
            return {"enabled": True, "provider": "groq",
                    "model": GROQ_MODEL, "reason": "configured"}
        return {"enabled": True, "provider": "watsonx",
                "model": WATSONX_MODEL_ID,
                "url": WATSONX_URL, "reason": "configured"}

    if not _bool("URJASETU_LLM_ENABLED", False):
        return {"enabled": False, "provider": LLM_PROVIDER,
                "reason": "URJASETU_LLM_ENABLED is not true"}
    if LLM_PROVIDER == "groq":
        return {"enabled": False, "provider": "groq",
                "reason": "GROQ_API_KEY is empty"}
    missing = []
    if not WATSONX_API_KEY:
        missing.append("WATSONX_API_KEY")
    if not WATSONX_PROJECT_ID:
        missing.append("WATSONX_PROJECT_ID")
    return {"enabled": False, "provider": "watsonx",
            "model": WATSONX_MODEL_ID,
            "reason": f"missing: {', '.join(missing)}"}


# ------------------------------------------------------------------ IBM App Configuration  (Sub-Task 3)

APPCONFIGURATION_API_KEY        = _str("APPCONFIGURATION_API_KEY")
APPCONFIGURATION_INSTANCE_ID    = _str("APPCONFIGURATION_INSTANCE_ID")
APPCONFIGURATION_REGION         = _str("APPCONFIGURATION_REGION", "us-south")
APPCONFIGURATION_COLLECTION_ID  = _str("APPCONFIGURATION_COLLECTION_ID", "urjasetu-engine")
APPCONFIGURATION_ENVIRONMENT_ID = _str("APPCONFIGURATION_ENVIRONMENT_ID", "dev")

_app_config_client = None      # set by _init_app_config()
_app_config_ready  = False     # True once the SDK connected successfully


def _init_app_config() -> None:
    """Initialise the IBM App Configuration SDK if credentials are present.

    Non-blocking: any failure (missing package, bad key, network error) is
    caught and logged to stderr, and all feature-flag reads fall back to
    environment-variable values. The engine never fails to start because of
    App Config being absent or misconfigured.
    """
    global _app_config_client, _app_config_ready
    if not (APPCONFIGURATION_API_KEY and APPCONFIGURATION_INSTANCE_ID):
        return  # credentials not configured — silent skip

    try:
        from ibm_appconfiguration import AppConfiguration  # optional SDK
        client = AppConfiguration.get_instance()
        client.init(
            region=APPCONFIGURATION_REGION,
            guid=APPCONFIGURATION_INSTANCE_ID,
            apikey=APPCONFIGURATION_API_KEY,
        )
        client.set_context(
            collection_id=APPCONFIGURATION_COLLECTION_ID,
            environment_id=APPCONFIGURATION_ENVIRONMENT_ID,
        )
        _app_config_client = client
        _app_config_ready = True
    except Exception as exc:                              # pragma: no cover
        import sys
        print(f"[settings] IBM App Configuration init failed (non-fatal): {exc}",
              file=sys.stderr)


def app_config_bool(flag_id: str, default: bool) -> bool:
    """Return the live boolean feature-flag value from IBM App Configuration.

    Falls back to `default` when App Config is unavailable or the flag is not
    found — preserving the existing env-var or config.yaml behaviour exactly.
    """
    if not _app_config_ready or _app_config_client is None:
        return default
    try:
        feature = _app_config_client.get_feature(flag_id)
        if feature is None:
            return default
        val = feature.get_current_value(entity_id="engine", entity_attributes={})
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("1", "true", "yes", "on")
    except Exception:
        return default


def app_config_number(flag_id: str, default: float) -> float:
    """Return the live numeric property value from IBM App Configuration."""
    if not _app_config_ready or _app_config_client is None:
        return default
    try:
        prop = _app_config_client.get_property(flag_id)
        if prop is None:
            return default
        val = prop.get_current_value(entity_id="engine", entity_attributes={})
        return float(val) if val is not None else default
    except Exception:
        return default


def app_config_string(flag_id: str, default: str) -> str:
    """Return the live string property value from IBM App Configuration."""
    if not _app_config_ready or _app_config_client is None:
        return default
    try:
        prop = _app_config_client.get_property(flag_id)
        if prop is None:
            return default
        val = prop.get_current_value(entity_id="engine", entity_attributes={})
        return str(val) if val is not None else default
    except Exception:
        return default


# Initialise App Config eagerly at import time.  Non-blocking — any error is
# caught inside _init_app_config() and logged, never propagated.
_init_app_config()

# ------------------------------------------------------------------ IBM Cloud Code Engine  (Sub-Task 5)

#: True when running inside IBM Cloud Code Engine.
#: Suppresses the Render keep-alive pinger (Code Engine never sleeps).
IBM_CODE_ENGINE = _bool("IBM_CODE_ENGINE", False)

# ------------------------------------------------------------------ server

PORT              = _int("PORT", 8000)
CORS_ORIGINS      = [o.strip() for o in _str("CORS_ORIGINS", "*").split(",") if o.strip()]
DAYS              = _int("URJASETU_DAYS", 30)
STREAM_CADENCE_S  = _float("URJASETU_STREAM_CADENCE_S", 3.5)
DERATE            = _float("URJASETU_DERATE", 1.0)
