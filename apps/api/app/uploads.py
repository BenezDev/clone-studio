"""Primitivas seguras para uploads locais.

O ``UploadFile`` do FastAPI limita RAM ao usar arquivo temporário, mas não
limita disco. Estas funções impõem um teto durante a cópia e removem qualquer
arquivo parcial em caso de erro.
"""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Callable


MIB = 1024 * 1024
MAX_TEMPLATE_UPLOAD_BYTES = 1024 * MIB
MAX_VOICE_UPLOAD_BYTES = 100 * MIB
# B-roll é material de apoio de poucos segundos; um teto menor que o de template
# reduz a superfície de um upload acidental de arquivo enorme.
MAX_BROLL_UPLOAD_BYTES = 512 * MIB
_COPY_CHUNK = MIB
_MULTIPART_OVERHEAD = MIB


class UploadTooLarge(ValueError):
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(
            f"Upload excede o limite de {max_bytes / MIB:.0f} MB."
        )


class _RequestBodyExceeded(BaseException):
    """Sinal interno que atravessa os handlers HTTP de exceção da aplicação."""


class RequestBodyLimitMiddleware:
    """Limita o corpo antes que o parser multipart o grave em temporário."""

    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def _limit(path: str) -> int | None:
        """Teto por rota de upload.

        Toda rota que receba `UploadFile` precisa aparecer aqui. Sem entrada, o
        parser multipart grava o corpo inteiro em disco antes de qualquer
        código do projeto rodar, e o teto do `copy_file_limited` chega tarde
        demais. `tests/unit/test_robustness.py` enumera as rotas e falha se
        alguma ficar de fora.
        """
        if path == "/api/templates/upload":
            return MAX_TEMPLATE_UPLOAD_BYTES + _MULTIPART_OVERHEAD
        if path == "/api/assets/broll/upload":
            return MAX_BROLL_UPLOAD_BYTES + _MULTIPART_OVERHEAD
        if path.startswith("/api/voice/profiles/") and path.endswith("/enroll"):
            return MAX_VOICE_UPLOAD_BYTES + _MULTIPART_OVERHEAD
        return None

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(str(scope.get("path", "")))
        if limit is None:
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > limit:
            await self._reject(send, limit)
            return

        received = 0
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestBodyExceeded
            return message

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyExceeded:
            if response_started:
                return
            await self._reject(send, limit)

    @staticmethod
    async def _reject(send: Callable[[dict[str, Any]], Awaitable[None]], limit: int) -> None:
        body = json.dumps(
            {"detail": f"Upload excede o limite de {(limit - _MULTIPART_OVERHEAD) / MIB:.0f} MB."}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def safe_upload_stem(filename: str | None, *, fallback: str) -> str:
    """Produz nome simples válido em POSIX e Windows, sem confiar no cliente."""
    raw = (filename or fallback).replace("\\", "/").rsplit("/", 1)[-1]
    stem = Path(raw).stem
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_").lower()
    normalized = normalized[:80] or fallback
    if normalized in {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }:
        normalized = f"upload-{normalized}"
    return normalized


def copy_file_limited(source: BinaryIO, destination: Path, max_bytes: int) -> int:
    """Copia no máximo ``max_bytes`` e nunca deixa um arquivo parcial."""
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            while True:
                block = source.read(_COPY_CHUNK)
                if not block:
                    break
                written += len(block)
                if written > max_bytes:
                    raise UploadTooLarge(max_bytes)
                handle.write(block)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return written


def unique_upload_path(directory: Path, stem: str, suffix: str) -> Path:
    """Escolhe um nome livre; a abertura exclusiva ainda resolve a corrida."""
    destination = directory / f"{stem}{suffix}"
    counter = 2
    while destination.exists():
        destination = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return destination
