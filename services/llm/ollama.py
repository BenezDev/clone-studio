"""Adapter do Ollama.

Nenhum modelo é fixado no código: os modelos instalados são descobertos em
tempo de execução e o usuário escolhe na interface. `model: auto` usa a lista
de preferências da configuração e cai no primeiro modelo disponível.

O Ollama é OPCIONAL. Sem ele, o usuário escreve o roteiro à mão e todo o
restante do pipeline funciona igual.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from core.config.loader import load_settings


class OllamaUnavailable(RuntimeError):
    """O serviço do Ollama não está acessível."""


@dataclass
class OllamaModel:
    name: str
    size_bytes: int = 0
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1e9


class OllamaClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        settings = load_settings()
        self.base_url = (base_url or settings.llm.base_url).rstrip("/")
        self.timeout = timeout or settings.llm.timeout_seconds
        self.preferred = settings.llm.preferred_models
        self._configured_model = settings.llm.model
        self.temperature = settings.llm.temperature

    # -- infraestrutura --------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(
                f"Ollama não está respondendo em {self.base_url}. "
                "Instale em https://ollama.com e rode `ollama serve`."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable(
                f"Ollama excedeu o tempo limite de {self.timeout}s."
            ) from exc
        response.raise_for_status()
        return response

    def available(self) -> bool:
        try:
            self._request("GET", "/api/tags")
            return True
        except (OllamaUnavailable, httpx.HTTPError):
            return False

    # -- modelos ---------------------------------------------------------

    def list_models(self) -> list[OllamaModel]:
        data = self._request("GET", "/api/tags").json()
        models: list[OllamaModel] = []
        for item in data.get("models", []):
            details = item.get("details") or {}
            models.append(
                OllamaModel(
                    name=item.get("name", ""),
                    size_bytes=int(item.get("size", 0)),
                    family=details.get("family", ""),
                    parameter_size=details.get("parameter_size", ""),
                    quantization=details.get("quantization_level", ""),
                )
            )
        return models

    def resolve_model(self) -> str:
        """Escolhe o modelo a usar.

        Ordem: o configurado explicitamente > a lista de preferências >
        o primeiro instalado.
        """
        if self._configured_model and self._configured_model != "auto":
            return self._configured_model

        installed = self.list_models()
        if not installed:
            raise OllamaUnavailable(
                "O Ollama está rodando, mas nenhum modelo foi baixado. "
                "Rode por exemplo: ollama pull qwen3:8b"
            )

        names = {m.name for m in installed}
        for preferred in self.preferred:
            if preferred in names:
                return preferred
            # Aceita variações de tag: 'qwen3:8b' casa com 'qwen3:8b-instruct'.
            base = preferred.split(":")[0]
            for name in sorted(names):
                if name.split(":")[0] == base:
                    return name
        return sorted(names)[0]

    # -- geração ---------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.resolve_model(),
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": (
                    self.temperature if temperature is None else temperature
                )
            },
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        data = self._request("POST", "/api/generate", json=payload).json()
        return data.get("response", "")

    def stream(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str | None = None,
        temperature: float | None = None,
    ) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model or self.resolve_model(),
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": (
                    self.temperature if temperature is None else temperature
                )
            },
        }
        if system:
            payload["system"] = system

        url = f"{self.base_url}/api/generate"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        piece = chunk.get("response", "")
                        if piece:
                            yield piece
                        if chunk.get("done"):
                            return
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(
                f"Ollama não está respondendo em {self.base_url}."
            ) from exc


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(raw: str) -> dict[str, Any]:
    """Extrai o objeto JSON da resposta do LLM.

    Modelos locais frequentemente envolvem o JSON em cercas de código ou
    adicionam texto antes/depois. Nunca se confia na saída bruta.
    """
    candidate = raw.strip()

    match = _JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(1).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"O modelo não devolveu JSON válido: {exc}"
            ) from exc

    raise ValueError("O modelo não devolveu nenhum objeto JSON.")
