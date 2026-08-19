"""Cache por hash de conteúdo.

Regra do projeto: se nada mudou, não recalcular. A chave é derivada dos
*inputs* (arquivos + parâmetros), nunca de timestamps — assim o cache continua
válido depois de copiar o projeto ou reinstalar a aplicação.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_CHUNK = 1024 * 1024


def hash_file(path: Path, *, partial: bool = True) -> str:
    """Hash de um arquivo.

    `partial=True` lê início, meio e fim + tamanho — o suficiente para
    identificar mídia grande sem varrer gigabytes a cada render.
    """
    digest = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    digest.update(str(size).encode())

    with path.open("rb") as handle:
        if not partial or size <= _CHUNK * 3:
            for block in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(block)
        else:
            for offset in (0, size // 2, max(0, size - _CHUNK)):
                handle.seek(offset)
                digest.update(handle.read(_CHUNK))
    return digest.hexdigest()


def hash_inputs(
    *,
    files: Iterable[Path] = (),
    params: dict[str, Any] | None = None,
    text: str = "",
) -> str:
    """Chave de cache combinando arquivos, parâmetros e texto."""
    digest = hashlib.blake2b(digest_size=16)
    for path in sorted(Path(f) for f in files):
        digest.update(path.name.encode())
        if path.exists():
            digest.update(hash_file(path).encode())
        else:
            digest.update(b"<missing>")
    if params:
        digest.update(
            json.dumps(params, sort_keys=True, ensure_ascii=False, default=str).encode()
        )
    if text:
        digest.update(text.encode("utf-8"))
    return digest.hexdigest()


@dataclass
class CacheEntry:
    key: str
    directory: Path
    metadata: dict[str, Any]

    @property
    def hit(self) -> bool:
        return (self.directory / "meta.json").exists()

    def file(self, name: str) -> Path:
        return self.directory / name

    def commit(self, metadata: dict[str, Any] | None = None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = dict(self.metadata)
        payload.update(metadata or {})
        payload["_cached_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        payload["_key"] = self.key
        (self.directory / "meta.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def load_metadata(self) -> dict[str, Any]:
        target = self.directory / "meta.json"
        if not target.exists():
            return {}
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def invalidate(self) -> None:
        if self.directory.exists():
            shutil.rmtree(self.directory, ignore_errors=True)


class CacheNamespace:
    """Um espaço de cache isolado (ex.: 'templates', 'voice', 'captions')."""

    def __init__(self, root: Path, name: str) -> None:
        self.root = root / name
        self.name = name

    def entry(
        self,
        *,
        files: Iterable[Path] = (),
        params: dict[str, Any] | None = None,
        text: str = "",
        label: str = "",
    ) -> CacheEntry:
        key = hash_inputs(files=files, params=params, text=text)
        directory = self.root / (f"{label}-{key}" if label else key)
        return CacheEntry(key=key, directory=directory, metadata={"namespace": self.name})

    def clear(self) -> int:
        """Apaga o namespace inteiro. Devolve quantas entradas removeu."""
        if not self.root.exists():
            return 0
        count = sum(1 for _ in self.root.iterdir())
        shutil.rmtree(self.root, ignore_errors=True)
        return count

    def size_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())


def get_cache(name: str) -> CacheNamespace:
    from core.storage.paths import get_paths

    return CacheNamespace(get_paths().cache, name)
