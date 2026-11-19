"""Reference targets and an optional localhost-only Ollama adapter."""

from dataclasses import dataclass
from http.client import HTTPException
import json
import re
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .catalog import SCENARIOS, SYSTEM_CONTEXT, Scenario

DEFAULT_MODEL = "llama3.2:3b"
OLLAMA_URL = "http://127.0.0.1:11434"
MAX_RESPONSE_BYTES = 2_000_000


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_local_opener = build_opener(ProxyHandler({}), NoRedirects())


class TargetError(RuntimeError):
    """A target failed to provide a usable response."""


class Target(Protocol):
    def complete(self, scenario: Scenario) -> str: ...


@dataclass(frozen=True)
class ReferenceTarget:
    """Deterministic simulation used to verify the evaluation pipeline.

    It is *not* an LLM and its performance is not an LLM benchmark.
    """

    mode: str

    def complete(self, scenario: Scenario) -> str:
        if scenario not in SCENARIOS:
            raise TargetError("Unknown evaluation scenario")
        if self.mode == "secure":
            return "Simulated public response: only public helpdesk information is included."
        if self.mode == "vulnerable":
            return f"Simulated response with fixture label: {scenario.marker}"
        raise TargetError("Unsupported reference target mode")


@dataclass(frozen=True)
class OllamaTarget:
    """Run fixed benign control exercises against an installed local model."""

    model: str = DEFAULT_MODEL
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", self.model):
            raise ValueError("Use a local Ollama model name such as llama3.2:3b.")
        if self.model.casefold().endswith(("-cloud", ":cloud")):
            raise ValueError("Choose an installed local model without a cloud tag.")
        if not 1 <= self.timeout_seconds <= 180:
            raise ValueError("Timeout must be between 1 and 180 seconds")

    def verify_ready(self) -> None:
        """Check the model once before starting a suite of requests."""
        result = self._request_json("tags", timeout=min(4.0, self.timeout_seconds))
        name = self.model if ":" in self.model else f"{self.model}:latest"
        aliases = (name, self.model)
        models = result.get("models")
        if not isinstance(models, list):
            raise TargetError("Ollama returned an invalid installed model list.")
        for model in models:
            if not isinstance(model, dict):
                continue
            if model.get("model") not in aliases and model.get("name") not in aliases:
                continue
            if model.get("remote_host") or model.get("remote_model"):
                raise TargetError("Choose an installed local model for this evaluation.")
            return
        raise TargetError(f"Model {self.model} is unavailable. Check the installed names with ollama list.")

    def complete(self, scenario: Scenario) -> str:
        if scenario not in SCENARIOS:
            raise TargetError("Unknown evaluation scenario")
        user_prompt = scenario.prompt
        if scenario.untrusted_context:
            user_prompt += "\n\nSynthetic reference material:\n" + scenario.untrusted_context
        result = self._request_json("chat", {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 160},
            "messages": [
                {"role": "system", "content": SYSTEM_CONTEXT},
                {"role": "user", "content": user_prompt},
            ],
        })
        message = result.get("message")
        value = message.get("content") if isinstance(message, dict) else None
        if result.get("done") is not True or result.get("done_reason") == "length":
            raise TargetError("Ollama did not complete the response within the generation limit.")
        if not isinstance(value, str) or not value.strip():
            raise TargetError("Ollama returned no usable response text.")
        return value

    def _request_json(
        self, endpoint: str, payload: dict | None = None, *, timeout: float | None = None
    ) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{OLLAMA_URL}/api/{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with _local_opener.open(request, timeout=timeout or self.timeout_seconds) as response:
                if response.status != 200:
                    raise TargetError(f"Ollama returned HTTP {response.status}.")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise TargetError("Ollama response exceeded the 2 MB limit.")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object")
            return result
        except HTTPError as exc:
            exc.close()
            raise TargetError(f"Ollama returned HTTP {exc.code}. Check that the model is available.") from exc
        except TimeoutError as exc:
            raise TargetError("Ollama timed out. Try a smaller local model.") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TargetError("Ollama timed out. Try a smaller local model.") from exc
            raise TargetError("Could not reach Ollama at 127.0.0.1:11434. Start Ollama and try again.") from exc
        except (ValueError, RecursionError) as exc:
            raise TargetError("Ollama returned malformed JSON.") from exc
        except (HTTPException, OSError) as exc:
            raise TargetError("The local Ollama connection ended before a complete response.") from exc

