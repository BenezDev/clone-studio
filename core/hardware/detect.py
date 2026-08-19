"""Detecção de hardware — somente stdlib.

Este módulo é executado pelo `install.sh` ANTES de qualquer ambiente virtual
existir. Por isso não pode importar nada fora da biblioteca padrão do Python.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Perfis de hardware
# ---------------------------------------------------------------------------

PROFILE_CPU_ONLY = "CPU_ONLY"
PROFILE_LOW_VRAM = "LOW_VRAM"
PROFILE_MID_VRAM = "MID_VRAM"
PROFILE_HIGH_VRAM = "HIGH_VRAM"
PROFILE_EXTREME = "EXTREME"

PROFILE_ORDER = [
    PROFILE_CPU_ONLY,
    PROFILE_LOW_VRAM,
    PROFILE_MID_VRAM,
    PROFILE_HIGH_VRAM,
    PROFILE_EXTREME,
]


def _run(cmd: list[str], timeout: int = 15) -> str | None:
    """Executa um comando e devolve stdout, ou None se falhar."""
    if not shutil.which(cmd[0]):
        return None
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------


@dataclass
class GPUInfo:
    vendor: str  # nvidia | amd | intel | apple | unknown
    name: str
    vram_mb: int | None = None
    driver: str | None = None
    compute: str | None = None  # "cuda 12.4", "rocm 6.1", "level-zero", ...
    discrete: bool = True
    usable_for_ml: bool = False
    notes: str = ""


@dataclass
class ToolInfo:
    name: str
    found: bool
    path: str | None = None
    version: str | None = None


@dataclass
class HardwareReport:
    os_name: str = ""
    os_version: str = ""
    kernel: str = ""
    arch: str = ""
    python_version: str = ""

    cpu_model: str = ""
    cpu_cores_physical: int | None = None
    cpu_threads: int | None = None
    cpu_flags_avx2: bool = False
    cpu_flags_avx512: bool = False

    ram_total_mb: int | None = None
    ram_available_mb: int | None = None
    swap_total_mb: int | None = None

    disk_total_gb: float | None = None
    disk_free_gb: float | None = None

    gpus: list[GPUInfo] = field(default_factory=list)
    cuda_available: bool = False
    cuda_version: str | None = None
    rocm_available: bool = False
    rocm_version: str | None = None

    tools: list[ToolInfo] = field(default_factory=list)

    profile: str = PROFILE_CPU_ONLY
    profile_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def tool(self, name: str) -> ToolInfo | None:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    @property
    def best_vram_mb(self) -> int:
        vrams = [g.vram_mb or 0 for g in self.gpus if g.usable_for_ml]
        return max(vrams) if vrams else 0


# ---------------------------------------------------------------------------
# Coletores
# ---------------------------------------------------------------------------


def _detect_os(report: HardwareReport) -> None:
    report.arch = platform.machine()
    report.kernel = platform.release()
    report.python_version = platform.python_version()

    system = platform.system()
    report.os_name = system
    report.os_version = platform.version()

    osr = Path("/etc/os-release")
    if system == "Linux" and osr.exists():
        data: dict[str, str] = {}
        for line in osr.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key] = value.strip().strip('"')
        report.os_name = data.get("PRETTY_NAME") or data.get("NAME") or system
        report.os_version = data.get("VERSION_ID", "")


def _detect_cpu(report: HardwareReport) -> None:
    report.cpu_threads = os.cpu_count()

    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        text = cpuinfo.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^model name\s*:\s*(.+)$", text, re.MULTILINE)
        if match:
            report.cpu_model = match.group(1).strip()

        # núcleos físicos = pares (physical id, core id) distintos
        cores: set[tuple[str, str]] = set()
        physical_id = core_id = None
        for line in text.splitlines():
            if line.startswith("physical id"):
                physical_id = line.split(":")[-1].strip()
            elif line.startswith("core id"):
                core_id = line.split(":")[-1].strip()
                if physical_id is not None:
                    cores.add((physical_id, core_id))
        if cores:
            report.cpu_cores_physical = len(cores)

        flags_match = re.search(r"^flags\s*:\s*(.+)$", text, re.MULTILINE)
        if flags_match:
            flags = set(flags_match.group(1).split())
            report.cpu_flags_avx2 = "avx2" in flags
            report.cpu_flags_avx512 = any(f.startswith("avx512") for f in flags)

    if not report.cpu_model:
        report.cpu_model = platform.processor() or "desconhecido"

    if platform.system() == "Windows":
        out = _run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Processor | Select-Object -First 1 "
                "Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress",
            ]
        )
        if out:
            try:
                cpu = json.loads(out)
                report.cpu_model = str(cpu.get("Name") or report.cpu_model).strip()
                report.cpu_cores_physical = int(cpu.get("NumberOfCores") or 0) or None
                report.cpu_threads = int(cpu.get("NumberOfLogicalProcessors") or 0) or report.cpu_threads
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        try:
            import ctypes

            # PF_AVX2_INSTRUCTIONS_AVAILABLE, definido pelo SDK do Windows.
            report.cpu_flags_avx2 = bool(
                ctypes.windll.kernel32.IsProcessorFeaturePresent(40)
            )
        except (AttributeError, OSError):
            pass


def _detect_memory(report: HardwareReport) -> None:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        values: dict[str, int] = {}
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.strip().split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0])  # kB
        if "MemTotal" in values:
            report.ram_total_mb = values["MemTotal"] // 1024
        if "MemAvailable" in values:
            report.ram_available_mb = values["MemAvailable"] // 1024
        if "SwapTotal" in values:
            report.swap_total_mb = values["SwapTotal"] // 1024
        return

    if platform.system() == "Windows":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong),
                    ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                report.ram_total_mb = status.total_phys // (1024 * 1024)
                report.ram_available_mb = status.avail_phys // (1024 * 1024)
                report.swap_total_mb = max(0, status.total_page - status.total_phys) // (1024 * 1024)
        except (AttributeError, OSError):
            pass


def _detect_disk(report: HardwareReport, target: Path) -> None:
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return
    report.disk_total_gb = round(usage.total / 1e9, 1)
    report.disk_free_gb = round(usage.free / 1e9, 1)


def _detect_nvidia(report: HardwareReport) -> None:
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return

    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        name, vram, driver = parts[0], parts[1], parts[2]
        try:
            vram_mb = int(float(vram))
        except ValueError:
            vram_mb = None
        report.gpus.append(
            GPUInfo(
                vendor="nvidia",
                name=name,
                vram_mb=vram_mb,
                driver=driver,
                compute="cuda",
                discrete=True,
                usable_for_ml=True,
            )
        )
    report.cuda_available = bool(report.gpus)

    # versão do CUDA runtime exposta pelo driver
    smi = _run(["nvidia-smi"])
    if smi:
        match = re.search(r"CUDA Version:\s*([\d.]+)", smi)
        if match:
            report.cuda_version = match.group(1)


def _detect_amd(report: HardwareReport) -> None:
    out = _run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"])
    if out:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = {}
        for key, card in data.items():
            if not key.lower().startswith("card"):
                continue
            name = (
                card.get("Card Series")
                or card.get("Card model")
                or card.get("Card SKU")
                or "AMD GPU"
            )
            vram_mb = None
            for field_name, value in card.items():
                if "vram Total Memory" in field_name:
                    try:
                        vram_mb = int(value) // (1024 * 1024)
                    except (TypeError, ValueError):
                        pass
            report.gpus.append(
                GPUInfo(
                    vendor="amd",
                    name=str(name),
                    vram_mb=vram_mb,
                    compute="rocm",
                    discrete=True,
                    usable_for_ml=True,
                )
            )
        report.rocm_available = True

    version_file = Path("/opt/rocm/.info/version")
    if version_file.exists():
        report.rocm_available = True
        report.rocm_version = version_file.read_text(encoding="utf-8", errors="replace").strip()


def _detect_pci_gpus(report: HardwareReport) -> None:
    """Fallback: descobre GPUs via lspci (pega iGPUs Intel/AMD)."""
    out = _run(["lspci"])
    if not out and platform.system() == "Windows":
        raw = _run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object "
                "Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress",
            ]
        )
        if raw:
            try:
                devices = json.loads(raw)
                if isinstance(devices, dict):
                    devices = [devices]
                known_names = {gpu.name.lower() for gpu in report.gpus}
                for device in devices:
                    name = str(device.get("Name") or "GPU desconhecida")
                    if name.lower() in known_names:
                        continue
                    lower = name.lower()
                    vendor = "intel" if "intel" in lower else (
                        "amd" if "amd" in lower or "radeon" in lower else (
                            "nvidia" if "nvidia" in lower else "unknown"
                        )
                    )
                    ram = device.get("AdapterRAM")
                    report.gpus.append(
                        GPUInfo(
                            vendor=vendor,
                            name=name,
                            vram_mb=int(ram) // (1024 * 1024) if ram else None,
                            driver=str(device.get("DriverVersion") or "") or None,
                            discrete=vendor not in {"intel", "unknown"},
                            usable_for_ml=False,
                            notes="Sem runtime CUDA/ROCm detectado para esta GPU.",
                        )
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return
    if not out:
        return

    known = {(g.vendor, g.name) for g in report.gpus}
    for line in out.splitlines():
        if not re.search(r"VGA compatible controller|3D controller|Display controller", line):
            continue
        desc = line.split(":", 2)[-1].strip()
        lower = desc.lower()

        if "nvidia" in lower:
            vendor = "nvidia"
        elif "amd" in lower or "ati " in lower or "advanced micro" in lower:
            vendor = "amd"
        elif "intel" in lower:
            vendor = "intel"
        else:
            vendor = "unknown"

        # Já reportada por nvidia-smi/rocm-smi? Não duplicar.
        if any(v == vendor for v, _ in known) and vendor in {"nvidia", "amd"}:
            continue

        discrete = not any(
            marker in lower
            for marker in ("iris", "uhd graphics", "hd graphics", "integrated", "raptor lake", "alder lake", "meteor lake")
        )

        gpu = GPUInfo(
            vendor=vendor,
            name=desc,
            discrete=discrete,
            usable_for_ml=False,
            notes="GPU integrada — sem CUDA/ROCm; usada apenas para display."
            if not discrete
            else "GPU detectada via lspci, sem runtime de compute identificado.",
        )
        if vendor == "intel":
            gpu.compute = "level-zero" if Path("/dev/dri").exists() else None
            gpu.notes = (
                "iGPU Intel. Não há suporte CUDA/ROCm. Aceleração só via "
                "OpenVINO/oneAPI, que nenhuma engine deste projeto exige."
            )
        report.gpus.append(gpu)


def _detect_tools(report: HardwareReport) -> None:
    python_tool = "python" if platform.system() == "Windows" else "python3"
    checks: list[tuple[str, list[str] | None, str | None]] = [
        ("ffmpeg", ["ffmpeg", "-version"], r"ffmpeg version (\S+)"),
        ("ffprobe", ["ffprobe", "-version"], r"ffprobe version (\S+)"),
        ("git", ["git", "--version"], r"git version (\S+)"),
        ("node", ["node", "--version"], r"v?(\S+)"),
        ("pnpm", ["pnpm", "--version"], r"(\S+)"),
        ("npm", ["npm", "--version"], r"(\S+)"),
        ("uv", ["uv", "--version"], r"uv (\S+)"),
        (python_tool, [sys.executable, "--version"], r"Python (\S+)"),
        ("ollama", ["ollama", "--version"], r"([\d.]+)"),
        ("docker", ["docker", "--version"], r"Docker version (\S+),"),
        ("nvidia-smi", None, None),
        ("rocm-smi", None, None),
    ]

    for name, cmd, pattern in checks:
        # Se o instalador encontrou Python pelo launcher `py`, `python.exe`
        # pode legitimamente não estar no PATH do Windows. O interpretador em
        # execução é a evidência correta e sempre tem caminho absoluto.
        path = sys.executable if name == python_tool else shutil.which(name)
        version = None
        if path and cmd:
            out = _run(cmd)
            if out and pattern:
                match = re.search(pattern, out)
                if match:
                    version = match.group(1)
        report.tools.append(
            ToolInfo(name=name, found=path is not None, path=path, version=version)
        )


# ---------------------------------------------------------------------------
# Classificação de perfil
# ---------------------------------------------------------------------------


def classify_profile(report: HardwareReport) -> tuple[str, str]:
    """Traduz o hardware detectado num perfil + explicação legível.

    As faixas são orientativas: cada engine declara sua necessidade real no
    `model_registry.yaml`; o perfil serve apenas para escolher bons padrões.
    """
    vram_mb = report.best_vram_mb
    if vram_mb <= 0:
        ram = report.ram_total_mb or 0
        return (
            PROFILE_CPU_ONLY,
            f"Nenhuma GPU com runtime de compute (CUDA/ROCm) encontrada. "
            f"Execução em CPU com {ram // 1024} GB de RAM.",
        )

    vram_gb = vram_mb / 1024
    if vram_gb <= 8:
        return PROFILE_LOW_VRAM, f"GPU com ~{vram_gb:.0f} GB de VRAM."
    if vram_gb <= 16:
        return PROFILE_MID_VRAM, f"GPU com ~{vram_gb:.0f} GB de VRAM."
    if vram_gb <= 32:
        return PROFILE_HIGH_VRAM, f"GPU com ~{vram_gb:.0f} GB de VRAM."
    return PROFILE_EXTREME, f"GPU com ~{vram_gb:.0f} GB de VRAM."


def _collect_warnings(report: HardwareReport) -> None:
    if report.profile == PROFILE_CPU_ONLY:
        report.warnings.append(
            "Sem GPU acelerada: o pipeline funciona, porém lip-sync e TTS rodam "
            "em CPU e são significativamente mais lentos que o tempo real."
        )
    ram_gb = (report.ram_total_mb or 0) / 1024
    if ram_gb < 16:
        report.warnings.append(
            f"RAM total de {ram_gb:.1f} GB. Em CPU os modelos vivem na RAM; "
            "recomenda-se manter swap ativo e usar o perfil de TTS 0.6B."
        )
    if report.disk_free_gb is not None and report.disk_free_gb < 30:
        report.warnings.append(
            f"Apenas {report.disk_free_gb:.0f} GB livres em disco. Os modelos "
            "base do MVP ocupam ~8 GB."
        )
    ffmpeg = report.tool("ffmpeg")
    if ffmpeg and not ffmpeg.found:
        report.warnings.append("FFmpeg ausente — obrigatório para qualquer render.")
    if not report.cpu_flags_avx2:
        report.warnings.append(
            "CPU sem AVX2: inferência em CPU será ainda mais lenta e algumas "
            "builds otimizadas (CTranslate2/llama.cpp) podem não funcionar."
        )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def detect(disk_target: Path | None = None) -> HardwareReport:
    """Executa a detecção completa de hardware."""
    report = HardwareReport()
    _detect_os(report)
    _detect_cpu(report)
    _detect_memory(report)
    _detect_disk(report, disk_target or Path.cwd())
    _detect_nvidia(report)
    _detect_amd(report)
    _detect_pci_gpus(report)
    _detect_tools(report)
    report.profile, report.profile_reason = classify_profile(report)
    _collect_warnings(report)
    return report


def save_report(report: HardwareReport, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_report(source: Path) -> HardwareReport | None:
    if not source.exists():
        return None
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    gpus = [GPUInfo(**g) for g in raw.pop("gpus", [])]
    tools = [ToolInfo(**t) for t in raw.pop("tools", [])]
    report = HardwareReport(**raw)
    report.gpus = gpus
    report.tools = tools
    return report


if __name__ == "__main__":  # pragma: no cover - utilitário de linha de comando
    import sys

    result = detect()
    if "--json" in sys.argv:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Sistema      : {result.os_name} ({result.arch}, kernel {result.kernel})")
        print(f"CPU          : {result.cpu_model}")
        print(
            f"               {result.cpu_cores_physical} núcleos / "
            f"{result.cpu_threads} threads | AVX2={result.cpu_flags_avx2} "
            f"AVX512={result.cpu_flags_avx512}"
        )
        print(
            f"RAM          : {(result.ram_total_mb or 0) / 1024:.1f} GB total, "
            f"{(result.ram_available_mb or 0) / 1024:.1f} GB disponível"
        )
        print(f"Disco        : {result.disk_free_gb} GB livres de {result.disk_total_gb} GB")
        for gpu in result.gpus:
            vram = f"{gpu.vram_mb} MB" if gpu.vram_mb else "VRAM desconhecida"
            print(f"GPU          : [{gpu.vendor}] {gpu.name} — {vram}")
            if gpu.notes:
                print(f"               {gpu.notes}")
        if not result.gpus:
            print("GPU          : nenhuma detectada")
        print(f"Perfil       : {result.profile} — {result.profile_reason}")
        print("Ferramentas  :")
        for tool in result.tools:
            mark = "ok" if tool.found else "--"
            version = f" {tool.version}" if tool.version else ""
            print(f"               [{mark}] {tool.name}{version}")
        for warning in result.warnings:
            print(f"Aviso        : {warning}")
