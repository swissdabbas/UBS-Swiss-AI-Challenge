"""One LLM client for both providers, with structured outputs, a spend cap and an audit trail.

Modes:
  openai  - OpenAI API (key from .env or api-key.sk)
  local   - any OpenAI-compatible endpoint (Ollama, vLLM, ...) hosted in Switzerland,
            configured through LOCAL_LLM_BASE_URL. Without it the mode is still
            honoured: no data leaves the machine and callers fall back to rules.
  off     - never call an LLM
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .. import config, db


class LLMUnavailable(RuntimeError):
    """No LLM for this mode; callers use their deterministic fallback."""


class BudgetExceeded(LLMUnavailable):
    """The per-run spend cap was reached."""


MODES = ("local", "openai", "off")


def describe_mode(mode: str) -> dict[str, Any]:
    models = config.llm_models()
    if mode == "openai":
        ok = bool(config.openai_api_key())
        return {
            "mode": mode,
            "available": ok,
            "provider": "OpenAI API",
            "model_fast": models["fast"],
            "model_reasoning": models["reasoning"],
            "note": "Transaction descriptions are sent to OpenAI." if ok else "No OpenAI key found (.env or api-key.sk).",
        }
    if mode == "local":
        local = config.local_llm()
        if local:
            return {
                "mode": mode,
                "available": True,
                "provider": f"Local LLM ({local['base_url']})",
                "model_fast": local["model"],
                "model_reasoning": local["model"],
                "note": "All data is processed by the Swiss-hosted local model.",
            }
        return {
            "mode": mode,
            "available": False,
            "provider": "Local processing",
            "model_fast": "rules",
            "model_reasoning": "rules",
            "note": "No local LLM endpoint configured, so AI steps use deterministic rules. No data leaves this machine.",
        }
    return {
        "mode": "off",
        "available": False,
        "provider": "Rules only",
        "model_fast": "rules",
        "model_reasoning": "rules",
        "note": "LLM disabled; deterministic rules only.",
    }


class LLMClient:
    def __init__(self, mode: str, run_id: str | None = None, client_id: str | None = None):
        if mode not in MODES:
            raise ValueError(f"unknown LLM mode {mode!r}")
        self.mode = mode
        self.run_id = run_id
        self.client_id = client_id
        self.spent_chf = 0.0
        s = config.settings()["llm"]
        self.cap_chf = float(s["max_spend_per_run_chf"])
        self.timeout = float(s["timeout_s"])
        self._client = None
        self._models = config.llm_models()
        self._local = config.local_llm()

    @property
    def available(self) -> bool:
        return describe_mode(self.mode)["available"]

    def model_for(self, tier: str) -> str:
        if self.mode == "local" and self._local:
            return self._local["model"]
        return self._models[tier]

    def _sdk(self):
        if self._client is None:
            from openai import OpenAI
            import os

            if self.mode == "openai":
                self._client = OpenAI(
                    api_key=config.openai_api_key(),
                    organization=os.environ.get("OPENAI_ORG_ID") or None,
                    project=os.environ.get("OPENAI_PROJECT_ID") or None,
                    timeout=self.timeout,
                    max_retries=2,
                )
            else:
                self._client = OpenAI(base_url=self._local["base_url"], api_key=self._local["api_key"], timeout=self.timeout)
        return self._client

    def _price_chf(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        s = config.settings()["llm"]
        table = s["pricing_usd_per_million_tokens"]
        if self.mode == "local":
            return 0.0
        price = table.get(model) or next((v for k, v in table.items() if model.startswith(k + "-")), None) or table["default"]
        usd = (prompt_tokens * price["input"] + completion_tokens * price["output"]) / 1_000_000
        return usd * float(s["usd_to_chf"])

    def structured(
        self,
        *,
        purpose: str,
        tier: str,
        system: str,
        user: Any,
        schema_name: str,
        schema: dict[str, Any],
        prompt_version: str,
    ) -> dict[str, Any]:
        """Call the model with a strict JSON schema and return the parsed object."""
        if not self.available:
            raise LLMUnavailable(describe_mode(self.mode)["note"])
        model = self.model_for(tier)
        user_text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
        # rough pre-flight estimate: 4 chars per token, output ~ half of input
        est_tokens = (len(system) + len(user_text)) // 4
        if self.spent_chf + self._price_chf(model, est_tokens, est_tokens // 2) > self.cap_chf:
            raise BudgetExceeded(f"spend cap of CHF {self.cap_chf:.2f} per run reached")

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_text}]
        input_json = json.dumps({"model": model, "messages": messages, "schema": schema_name}, ensure_ascii=False)
        input_hash = hashlib.sha256(input_json.encode()).hexdigest()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        }
        if self.mode == "openai" and (model.startswith("gpt-5") or model.startswith("o")) and "chat" not in model:
            kwargs["reasoning_effort"] = config.settings()["llm"]["reasoning_effort"][tier]

        t0 = time.monotonic()
        try:
            resp = self._sdk().chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            parsed = json.loads(content)
        except Exception as exc:  # audited, then surfaced as "unavailable" so callers fall back
            self._audit(purpose, model, prompt_version, input_hash, input_json, None, 0, 0, 0.0, t0, "error", repr(exc)[:2000])
            raise LLMUnavailable(f"LLM call failed: {exc}") from exc

        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        cost = self._price_chf(model, pt, ct)
        self.spent_chf += cost
        self._audit(purpose, model, prompt_version, input_hash, input_json, content, pt, ct, cost, t0, "ok", None)
        return parsed

    def _audit(self, purpose, model, prompt_version, input_hash, input_json, output, pt, ct, cost, t0, status, error):
        db.execute(
            """INSERT INTO llm_audit (ts, run_id, client_id, purpose, provider, model, prompt_version, input_sha256,
               input_json, output_json, prompt_tokens, completion_tokens, cost_chf, latency_ms, status, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                db.now_iso(), self.run_id, self.client_id, purpose, self.mode, model, prompt_version, input_hash,
                input_json, output, pt, ct, round(cost, 6), int((time.monotonic() - t0) * 1000), status, error,
            ),
        )
