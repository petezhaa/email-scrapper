"""Loads configuration (config.yaml) and secrets (.env) from the project root."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

# Project root: config.yaml, .env, data/, drafts/, sent/, resume/ all live here.
ROOT = Path(__file__).resolve().parent.parent

_DATA_DIRS = ("data", "drafts", "sent", "resume")


def bundled_dir(name: str) -> Path:
    """Path to a resource folder inside the src/ package (templates / static)."""
    return Path(__file__).resolve().parent / name


def bootstrap() -> None:
    """Ensure the data folders exist so first run doesn't trip on missing dirs."""
    for d in _DATA_DIRS:
        (ROOT / d).mkdir(parents=True, exist_ok=True)


class ConfigError(RuntimeError):
    pass


class PipelineError(RuntimeError):
    """Raised for user-facing failures (missing key, no targets, etc.).

    The CLI catches it and prints a clean message; the web app surfaces it as
    a job error instead of a stack trace.
    """


def load_config() -> dict:
    """Read config.yaml from the project root."""
    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        raise ConfigError(f"Missing config file: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read_env_file() -> dict:
    """Parse .env into a dict (KEY -> value), stripping quotes/whitespace."""
    path = ROOT / ".env"
    vals: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def load_secrets() -> dict:
    """Return secrets, preferring a non-empty value in .env, else the environment.

    Re-reads .env on every call so settings saved via the web UI take effect
    immediately, without a blank .env line clobbering a real shell export.
    """
    f = _read_env_file()

    def get(key: str) -> str:
        return f.get(key, "").strip() or os.getenv(key, "").strip()

    return {
        "anthropic_api_key": get("ANTHROPIC_API_KEY"),
        "anthropic_auth_token": get("ANTHROPIC_AUTH_TOKEN"),
        "anthropic_base_url": get("ANTHROPIC_BASE_URL"),
        "openai_api_key": get("OPENAI_API_KEY"),       # direct OpenAI key (for GPT writer)
        "openai_base_url": get("OPENAI_BASE_URL"),     # optional override (default: api.openai.com)
        "gemini_api_key": get("GEMINI_API_KEY"),       # direct Google key (for Gemini reviewer)
        "gmail_address": get("GMAIL_ADDRESS"),
        "gmail_app_password": get("GMAIL_APP_PASSWORD"),
    }


def has_anthropic_credential(secrets: dict | None = None) -> bool:
    s = secrets or load_secrets()
    return bool(s["anthropic_api_key"] or s["anthropic_auth_token"])


def build_anthropic_client():
    """Construct the Anthropic client, honouring a custom base URL + auth.

    - ANTHROPIC_BASE_URL (optional): point at a compatible endpoint, e.g. an
      internal NVIDIA inference gateway, instead of api.anthropic.com.
    - Auth: ANTHROPIC_AUTH_TOKEN (sent as a Bearer token) is used if set,
      otherwise ANTHROPIC_API_KEY (sent as x-api-key). Some gateways want one,
      some want the other — set whichever your endpoint expects.
    """
    import anthropic

    s = load_secrets()
    kwargs: dict = {}
    if s["anthropic_base_url"]:
        kwargs["base_url"] = s["anthropic_base_url"]
    if s["anthropic_auth_token"]:
        kwargs["auth_token"] = s["anthropic_auth_token"]
    elif s["anthropic_api_key"]:
        kwargs["api_key"] = s["anthropic_api_key"]
    else:
        raise PipelineError(
            "No Anthropic credential set. Put a key in .env (ANTHROPIC_API_KEY) "
            "or a token (ANTHROPIC_AUTH_TOKEN) for your endpoint."
        )
    return anthropic.Anthropic(**kwargs)


def _gateway_client(s: dict, missing_hint: str):
    """OpenAI-compatible client pointed at the shared gateway (ANTHROPIC_BASE_URL).

    The gateway serves every provider behind one bearer token, using
    provider-prefixed model ids (e.g. "azure/openai/gpt-4o"). The OpenAI SDK
    needs /v1 appended to the base URL.
    """
    import openai

    api_key = s["anthropic_auth_token"] or s["anthropic_api_key"]
    if not api_key:
        raise PipelineError(missing_hint)
    base = s["anthropic_base_url"].rstrip("/") + "/v1"
    return openai.OpenAI(base_url=base, api_key=api_key)


def build_writer_client():
    """OpenAI client for the GPT writer step.

    Priority:
      1. Gateway (ANTHROPIC_BASE_URL set) — same bearer token as Claude. The
         model ids in config.yaml are gateway-prefixed ("azure/openai/gpt-4o"),
         which a direct OpenAI key can't serve, so the gateway must win when
         one is configured.
      2. OPENAI_API_KEY → direct OpenAI (api.openai.com or OPENAI_BASE_URL override)
    """
    import openai

    s = load_secrets()
    if s["anthropic_base_url"]:
        return _gateway_client(
            s, "No gateway credential found. Set ANTHROPIC_API_KEY in .env."
        )
    if s["openai_api_key"]:
        return openai.OpenAI(
            api_key=s["openai_api_key"],
            base_url=s["openai_base_url"] or None,  # None → default api.openai.com
        )
    raise PipelineError("No OpenAI or gateway credential found. Set OPENAI_API_KEY in .env.")


def build_reviewer_client():
    """OpenAI-compatible client for the Gemini reviewer step.

    Priority:
      1. Gateway (ANTHROPIC_BASE_URL set) — same bearer token as Claude; the
         reviewer model id in config.yaml is gateway-prefixed.
      2. GEMINI_API_KEY → Google's OpenAI-compatible endpoint
    """
    import openai

    s = load_secrets()
    if s["anthropic_base_url"]:
        return _gateway_client(
            s, "No gateway credential found. Set ANTHROPIC_API_KEY in .env."
        )
    if s["gemini_api_key"]:
        return openai.OpenAI(
            api_key=s["gemini_api_key"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    raise PipelineError("No Gemini or gateway credential found. Set GEMINI_API_KEY in .env.")


def build_openai_client():
    """Kept for backward compatibility — returns the writer client."""
    return build_writer_client()


def resolve(path_str: str) -> Path:
    """Resolve a config-relative path against the project root."""
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p


def save_config(cfg: dict) -> None:
    """Write config.yaml back to the project root (used by the web UI)."""
    with (ROOT / "config.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)


def update_env(updates: dict) -> None:
    """Update keys in .env, preserving existing keys (e.g. the shared API key).

    Creates .env from nothing if it doesn't exist. Used by the web UI to store
    each friend's Gmail address + app password locally without touching the
    pre-distributed ANTHROPIC_API_KEY.
    """
    env_path = ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    # Sanitize before writing: a CR/LF embedded in a value could inject an
    # extra KEY=value line into .env, and an '=' in a key would corrupt it.
    remaining: dict[str, str] = {}
    for key, val in updates.items():
        if "=" in key:
            raise ConfigError(f"Invalid .env key (contains '='): {key!r}")
        remaining[key] = str(val).replace("\r", "").replace("\n", "")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")

