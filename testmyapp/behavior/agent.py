"""Choix facultatif parmi les contrôles déjà observés par le mode exploratoire."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from http.client import HTTPException
from urllib.request import HTTPRedirectHandler, Request, build_opener

from testmyapp.config import AgentSettings


class RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AgentChoice:
    def __init__(self, settings: AgentSettings, private_values: set[str]) -> None:
        self.settings = settings
        self.remaining = settings.max_requests
        self.failed = False
        self.redactions = re.compile(
            "|".join(re.escape(value) for value in sorted(private_values, key=lambda v: (-len(v), v))),
            re.IGNORECASE,
        ) if private_values else None

    def _text(self, text: str, limit: int) -> str:
        if self.redactions:
            text = self.redactions.sub("[valeur masquée]", text)
        return " ".join(text.split())[:limit]

    async def choose(self, goal: str, candidates: list[dict]) -> tuple[int | None, dict]:
        if self.failed or self.remaining <= 0:
            return None, {"source": "weighted", "reason": "budget_or_provider"}
        self.remaining -= 1
        started = time.monotonic()
        available = [{"index": i, "role": item["role"], "label": self._text(item["name"], 120)}
                     for i, item in enumerate(candidates)]
        try:
            response = await asyncio.wait_for(asyncio.to_thread(
                self._request, {"goal": self._text(goal, 350), "candidates": available},
            ), timeout=self.settings.timeout_seconds)
            message = response["choices"][0]["message"]
            selection = json.loads(message["content"])["index"]
            if type(selection) is not int or not 0 <= selection < len(candidates):
                raise ValueError("choix hors liste")
            usage = response.get("usage")
            if not isinstance(usage, dict):
                usage = {}
            input_tokens = self._token_count(usage.get("prompt_tokens"))
            output_tokens = self._token_count(usage.get("completion_tokens"))
            cost = (input_tokens * self.settings.input_price_per_million
                    + output_tokens * self.settings.output_price_per_million) / 1_000_000
            return selection, {"source": "agent", "latency_ms": round((time.monotonic() - started) * 1000),
                               "input_tokens": input_tokens, "output_tokens": output_tokens,
                               "estimated_cost": cost}
        except (OSError, HTTPException, ValueError, TypeError, KeyError, IndexError, UnicodeError):
            self.failed = True
            return None, {"source": "weighted", "reason": "provider_failure",
                          "latency_ms": round((time.monotonic() - started) * 1000)}

    @staticmethod
    def _token_count(raw) -> int:
        return raw if type(raw) is int and 0 <= raw <= 1_000_000 else 0

    def _request(self, observation: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key_env:
            token = os.environ.get(self.settings.api_key_env)
            if not token:
                raise ValueError("clé fournisseur absente")
            headers["Authorization"] = f"Bearer {token}"
        data = {
            "model": self.settings.model,
            "temperature": 0,
            "max_tokens": 80,
            "messages": [
                {"role": "system", "content":
                 'Choisis le contrôle qui correspond le mieux au but. Réponds uniquement par {"index": nombre}.'},
                {"role": "user", "content": json.dumps(observation, ensure_ascii=False)},
            ],
        }
        request = Request(self.settings.endpoint, data=json.dumps(data).encode("utf-8"), headers=headers)
        with build_opener(RefuseRedirect()).open(request, timeout=self.settings.timeout_seconds) as response:
            return json.loads(response.read(256 * 1024))
