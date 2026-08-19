"""Cliente HTTP do ComfyUI.

O ComfyUI roda como serviço separado; a aplicação fala com ele apenas por API.
Nada aqui importa código do ComfyUI, e os workflows ficam versionados como JSON
em `workflows/comfyui/` — o acoplamento é ao formato de workflow, não aos
internals do projeto.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from core.storage.paths import get_paths

ProgressCallback = Callable[[float, str], None]


class ComfyUIUnavailable(RuntimeError):
    pass


class ComfyUIError(RuntimeError):
    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class ComfyUIClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    # -- estado ----------------------------------------------------------

    def online(self) -> bool:
        try:
            with httpx.Client(timeout=2.0) as client:
                return client.get(f"{self.base_url}/system_stats").status_code == 200
        except httpx.HTTPError:
            return False

    def system_stats(self) -> dict[str, Any]:
        return self._get("/system_stats")

    def object_info(self) -> dict[str, Any]:
        """Nós disponíveis — usado para checar se os nós do Wan2.2 existem."""
        return self._get("/object_info")

    def _get(self, path: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}{path}")
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as exc:
            raise ComfyUIUnavailable(
                f"ComfyUI não responde em {self.base_url}. "
                "Suba o serviço antes de usar B-roll generativo."
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"Falha ao consultar {path}: {exc}") from exc

    # -- workflows -------------------------------------------------------

    @staticmethod
    def load_workflow(name: str) -> dict[str, Any]:
        path = get_paths().workflows / "comfyui" / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Workflow '{name}' não encontrado em {path}. "
                "Os workflows do projeto ficam versionados em workflows/comfyui/."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def apply_inputs(
        workflow: dict[str, Any], overrides: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Aplica valores aos nós do workflow.

        `overrides` mapeia id_do_nó -> {campo: valor}. Um id inexistente é erro:
        falhar aqui é muito melhor que gerar um vídeo com o prompt errado.
        """
        patched = json.loads(json.dumps(workflow))
        for node_id, fields in overrides.items():
            node = patched.get(node_id)
            if node is None:
                raise ComfyUIError(
                    f"O workflow não tem o nó '{node_id}'. "
                    "Ele provavelmente foi editado ou é de outra versão."
                )
            node.setdefault("inputs", {}).update(fields)
        return patched

    def queue(self, workflow: dict[str, Any]) -> str:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": workflow, "client_id": self.client_id},
                )
                response.raise_for_status()
                return response.json()["prompt_id"]
        except httpx.ConnectError as exc:
            raise ComfyUIUnavailable(
                f"ComfyUI não responde em {self.base_url}."
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = None
            try:
                detail = exc.response.json()
            except (ValueError, json.JSONDecodeError):
                detail = exc.response.text
            raise ComfyUIError(
                "O ComfyUI recusou o workflow.", detail=detail
            ) from exc

    def wait(
        self,
        prompt_id: str,
        *,
        poll_seconds: float = 1.5,
        timeout: float = 3600,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Aguarda a execução terminar e devolve o histórico do prompt."""
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            history = self._get(f"/history/{prompt_id}")
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("completed") or entry.get("outputs"):
                    return entry
                if status.get("status_str") == "error":
                    raise ComfyUIError(
                        "A execução do workflow falhou no ComfyUI.",
                        detail=status,
                    )
            if on_progress:
                elapsed = time.monotonic() - started
                on_progress(
                    min(0.95, elapsed / max(timeout, 1)),
                    f"gerando… {elapsed:.0f}s",
                )
            time.sleep(poll_seconds)

        raise ComfyUIError(
            f"O workflow excedeu {timeout:.0f}s sem concluir."
        )

    def download_output(self, entry: dict[str, Any], destination: Path) -> Path:
        """Busca o primeiro vídeo/imagem produzido e grava no destino."""
        outputs = entry.get("outputs", {})
        for node_output in outputs.values():
            for key in ("gifs", "videos", "images"):
                for item in node_output.get(key, []) or []:
                    return self._fetch_file(item, destination)
        raise ComfyUIError(
            "O workflow terminou sem produzir nenhum arquivo de saída.",
            detail=list(outputs),
        )

    def _fetch_file(self, item: dict[str, Any], destination: Path) -> Path:
        params = {
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=300.0) as client:
            with client.stream("GET", f"{self.base_url}/view", params=params) as stream:
                stream.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in stream.iter_bytes(1 << 20):
                        handle.write(chunk)
        return destination
