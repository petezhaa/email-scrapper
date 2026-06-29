"""Loads configuration (config.yaml) and secrets (.env).

Works both in development (running from the source folder) and as a packaged
.exe/.app (PyInstaller). When frozen:
  - RESOURCE_DIR is the read-only bundle PyInstaller unpacks (sys._MEIPASS),
    holding default config.yaml/.env and the templates/static.
  - ROOT is a writable folder next to the executable, where the user's edited
    config, .env (with their Gmail creds), drafts, and resume actually live.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # Read-only files bundled into the executable, unpacked at runtime.
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # Writable data lives in a folder next to the .exe/.app so it persists and
    # the user can find their drafts. (_MEIPASS is wiped when the app closes.)
    ROOT = Path(sys.executable).resolve().parent / "ResearchOutreach-data"
else:
    # Dev: source layout. Resources and writable data are both the repo root.
    RESOURCE_DIR = Path(__file__).resolve().parent.parent
    ROOT = RESOURCE_DIR

# Files copied from the bundle into the writable ROOT on first run.
_DEFAULT_FILES = ("config.yaml", ".env", "data/profile.md", "data/directory_urls.txt")
_DATA_DIRS = ("data", "drafts", "sent", "resume")


def bundled_dir(name: str) -> Path:
    """Path to a bundled read-only resource folder (templates / static)."""
    if FROZEN:
        return RESOURCE_DIR / name
    # Dev: templates/ and static/ live inside the src/ package.
    return Path(__file__).resolve().parent / name


def bootstrap() -> None:
    """Ensure the writable ROOT exists and seed default files on first run.

    No-op in dev (defaults already exist and are skipped). For a packaged app,
    this copies bundled defaults — including the shared API key in .env — next
    to the executable the first time it runs.
    """
    ROOT.mkdir(parents=True, exist_ok=True)
    for d in _DATA_DIRS:
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    if not FROZEN:
        return  # dev: nothing to seed
    for rel in _DEFAULT_FILES:
        dst = ROOT / rel
        src = RESOURCE_DIR / rel
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)


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


def build_writer_client():
    """OpenAI client for the GPT writer step.

    Priority:
      1. OPENAI_API_KEY → direct OpenAI (api.openai.com or OPENAI_BASE_URL override)
      2. NVIDIA gateway  → same bearer token as Claude, ANTHROPIC_BASE_URL as base
    """
    import openai

    s = load_secrets()
    if s["openai_api_key"]:
        return openai.OpenAI(
            api_key=s["openai_api_key"],
            base_url=s["openai_base_url"] or None,  # None → default api.openai.com
        )
    # Fall back to NVIDIA gateway — OpenAI SDK needs /v1 appended to the base URL
    api_key = s["anthropic_auth_token"] or s["anthropic_api_key"]
    if not api_key:
        raise PipelineError("No OpenAI or NVIDIA credential found. Set OPENAI_API_KEY in .env.")
    nvidia_base = (s["anthropic_base_url"] or "").rstrip("/") + "/v1"
    return openai.OpenAI(base_url=nvidia_base, api_key=api_key)


def build_reviewer_client():
    """OpenAI-compatible client for the Gemini reviewer step.

    Priority:
      1. GEMINI_API_KEY → Google's OpenAI-compatible endpoint
      2. NVIDIA gateway  → same bearer token as Claude, ANTHROPIC_BASE_URL as base
    """
    import openai

    s = load_secrets()
    if s["gemini_api_key"]:
        return openai.OpenAI(
            api_key=s["gemini_api_key"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    # Fall back to NVIDIA gateway — OpenAI SDK needs /v1 appended to the base URL
    api_key = s["anthropic_auth_token"] or s["anthropic_api_key"]
    if not api_key:
        raise PipelineError("No Gemini or NVIDIA credential found. Set GEMINI_API_KEY in .env.")
    nvidia_base = (s["anthropic_base_url"] or "").rstrip("/") + "/v1"
    return openai.OpenAI(base_url=nvidia_base, api_key=api_key)


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

    remaining = dict(updates)
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

