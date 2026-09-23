"""Configuration: YAML files in config/ plus secrets from .env / api-key.sk."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines); real env vars take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv(ROOT / ".env")


def load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    s = load_yaml("settings.yaml")
    cap = os.environ.get("OPENAI_MAX_SPEND_PER_RUN_CHF")
    if cap:
        s["llm"]["max_spend_per_run_chf"] = float(cap)
    mode = os.environ.get("LLM_DEFAULT_MODE")
    if mode:
        s["llm"]["default_mode"] = mode
    return s


def path(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def openai_api_key() -> str | None:
    """OPENAI_API_KEY from the environment, else the first sk- token in api-key.sk."""
    key = os.environ.get("OPENAI_API_KEY")
    if key and key != "sk-...":
        return key
    key_file = ROOT / "api-key.sk"
    if key_file.exists():
        raw = key_file.read_text(encoding="utf-8").strip()
        for token in raw.replace("=", " ").split():
            if token.startswith("sk-"):
                return token
        return raw or None
    return None


def llm_models() -> dict[str, str]:
    return {
        "fast": os.environ.get("OPENAI_MODEL_FAST", "gpt-5-mini"),
        "reasoning": os.environ.get("OPENAI_MODEL_REASONING", "gpt-5"),
    }


def local_llm() -> dict[str, str] | None:
    base = os.environ.get("LOCAL_LLM_BASE_URL")
    if not base:
        return None
    return {
        "base_url": base,
        "model": os.environ.get("LOCAL_LLM_MODEL", "llama3.1:8b"),
        "api_key": os.environ.get("LOCAL_LLM_API_KEY", "not-needed"),
    }
