"""Model providers for synthesis and the judge (docs/design.md §5.3).

One small interface, so the wiki is written the same way whichever model writes
it, and the same inputs can be synthesised by two providers to tell the
generator's quality from one model's.

    provider_for("claude-opus-5")          Anthropic
    provider_for("openai:<model>")         OpenAI

Each returns JSON matching a JSON Schema. The schema must satisfy both APIs'
strict structured-output rules: every object `additionalProperties: false`,
every property listed in `required`. Each SDK is an optional dependency
(`kubewiki[anthropic]`, `kubewiki[openai]`), imported only when that provider is used.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

#: $ per million tokens (input, output), for reporting and --max-usd. A model
#: missing here reports tokens and an unknown cost rather than a wrong one.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float | None = None


class Provider(Protocol):
    model: str

    def complete(self, system: str, prompt: str, schema: dict,
                 name: str) -> tuple[dict, Usage]: ...


def price(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    return round((input_tokens * p[0] + output_tokens * p[1]) / 1e6, 4)


class AnthropicProvider:
    """Claude, through the Messages API with a JSON-Schema output format.

    Streamed: a full synthesis can run to tens of thousands of output tokens,
    past what a single non-streaming request should wait for.
    """

    def __init__(self, model: str, client: Any = None, max_tokens: int = 64000):
        self.model = model
        self.max_tokens = max_tokens
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, prompt: str, schema: dict, name: str) -> tuple[dict, Usage]:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            message = stream.get_final_message()
        if getattr(message, "stop_reason", None) == "refusal":
            raise RuntimeError(f"{self.model} declined the request")
        text = next(b.text for b in message.content if getattr(b, "type", None) == "text")
        u = message.usage
        usage = Usage(u.input_tokens, u.output_tokens,
                      price(self.model, u.input_tokens, u.output_tokens))
        return json.loads(text), usage


class OpenAIProvider:
    """OpenAI, through the Responses API with a strict JSON-Schema text format."""

    def __init__(self, model: str, client: Any = None):
        self.model = model
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import openai

            self._client = openai.OpenAI()
        return self._client

    def complete(self, system: str, prompt: str, schema: dict, name: str) -> tuple[dict, Usage]:
        response = self.client.responses.create(
            model=self.model,
            input=[{"role": "system", "content": system},
                   {"role": "user", "content": prompt}],
            text={"format": {"type": "json_schema", "name": name, "schema": schema,
                             "strict": True}},
        )
        u = response.usage
        usage = Usage(u.input_tokens, u.output_tokens,
                      price(f"openai:{self.model}", u.input_tokens, u.output_tokens))
        return json.loads(response.output_text), usage


def provider_for(model: str, client: Any = None) -> Provider:
    if model.startswith("openai:"):
        return OpenAIProvider(model.split(":", 1)[1], client=client)
    return AnthropicProvider(model, client=client)


def has_credentials(model: str) -> bool:
    key = "OPENAI_API_KEY" if model.startswith("openai:") else "ANTHROPIC_API_KEY"
    return bool(os.environ.get(key))
