"""Thin wrapper around the Anthropic Messages API for JSON-producing pipeline stages.

- Structured outputs (output_config.format json_schema) so every stage returns valid JSON.
- The stage's system prompt is cached (it is identical across calls in a run).
- Server-side refusal fallbacks are on by default (config: llm.fallbacks).
- Every call is costed into data/cost-log.jsonl; daily and per-story caps fail closed.
- A mock can be injected for tests and offline demos (no key, no cost).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Callable

from . import ROOT, config
from .util import iso, sha256

COST_LOG = ROOT / "data" / "cost-log.jsonl"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class Refused(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


MockFn = Callable[[str, str, str, dict, dict], dict]


class LLM:
    def __init__(self, mock: MockFn | None = None):
        self.cfg = config.pipeline()["llm"]
        self.mock = mock
        self._client = None
        self._fallbacks_ok = bool(self.cfg.get("fallbacks"))
        self.story_spend: dict[str, float] = {}

    # ------------------------------------------------------------------
    def _anthropic(self):
        if self._client is None:
            import anthropic  # imported lazily so offline tools work without the SDK configured
            self._client = anthropic.Anthropic()
        return self._client

    def _price(self, model: str) -> dict:
        prices = self.cfg.get("prices_usd_per_mtok", {})
        return prices.get(model) or prices.get(self.cfg.get("default_model"), {"input": 0, "output": 0})

    def spent_today_eur(self) -> float:
        if not COST_LOG.exists():
            return 0.0
        today = date.today().isoformat()
        total = 0.0
        with open(COST_LOG, encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                if e.get("at", "").startswith(today):
                    total += e.get("eur", 0.0)
        return total

    def _log(self, entry: dict) -> None:
        COST_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------------
    def json(self, stage: str, system: str, user: str, schema: dict, *,
             story_id: str | None = None, context: dict | None = None) -> dict:
        st = self.cfg["stages"][stage]
        if self.mock:
            out = self.mock(stage, system, user, schema, context or {})
            self._log({"at": iso(), "stage": stage, "model": "mock", "story": story_id, "eur": 0.0})
            return out

        cap = float(self.cfg.get("daily_spend_cap_eur", 0) or 0)
        if cap and self.spent_today_eur() >= cap:
            raise BudgetExceeded(f"daily AI spend cap €{cap:.2f} reached")
        story_cap = float(self.cfg.get("per_story_cap_eur", 0) or 0)
        if story_id and story_cap and self.story_spend.get(story_id, 0.0) >= story_cap:
            raise BudgetExceeded(f"per-story cap €{story_cap:.2f} reached for {story_id}")

        import anthropic

        params = dict(
            model=st["model"],
            max_tokens=int(st.get("max_tokens", 16000)),
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": st.get("effort", "medium"),
                           "format": {"type": "json_schema", "schema": schema}},
        )
        client = self._anthropic()
        try:
            if self._fallbacks_ok:
                resp = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks=self.cfg["fallbacks"], **params)
            else:
                resp = client.messages.create(**params)
        except anthropic.BadRequestError as e:
            if self._fallbacks_ok and "fallback" in str(e).lower():
                self._fallbacks_ok = False  # this account/model doesn't take fallbacks; carry on without
                resp = client.messages.create(**params)
            else:
                raise LLMError(f"{stage}: bad request: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"{stage}: rate limited; retry later") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"{stage}: network error: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"{stage}: API error {e.status_code}: {e}") from e

        usage = resp.usage
        price = self._price(getattr(resp, "model", st["model"]))
        usd = (
            (usage.input_tokens or 0) * price.get("input", 0)
            + (usage.output_tokens or 0) * price.get("output", 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * price.get("cache_write", price.get("input", 0))
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * price.get("cache_read", 0)
        ) / 1_000_000
        eur = usd * float(self.cfg.get("usd_to_eur", 0.9))
        if story_id:
            self.story_spend[story_id] = self.story_spend.get(story_id, 0.0) + eur
        self._log({
            "at": iso(), "stage": stage, "model": getattr(resp, "model", st["model"]), "story": story_id,
            "request_id": getattr(resp, "_request_id", None), "stop": resp.stop_reason,
            "in": usage.input_tokens, "out": usage.output_tokens,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0),
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0),
            "prompt_sha": sha256(system)[:12], "eur": round(eur, 5),
        })

        if resp.stop_reason == "refusal":
            raise Refused(f"{stage}: model declined ({getattr(resp, 'stop_details', None)})")
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"{stage}: output hit max_tokens")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"{stage}: response was not valid JSON") from e


def load_prompt(name: str) -> tuple[str, str]:
    """Returns (prompt text, version id). Prompts are versioned by content hash."""
    text = (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return text, sha256(text)[:12]
