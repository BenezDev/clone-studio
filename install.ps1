param(
    [ValidateSet("", "backend", "qwen-tts", "whisper", "musetalk", "frontend")]
    [string]$Only = "",
    [switch]$WithMuseTalk,
    [switch]$SkipFrontend,
    [switch]$NoModels,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Envs = Join-Path $Root ".envs"
$env:CLONE_STUDIO_ROOT = $Root
$env:PYTHONUTF8 = "1"
$PathSeparator = [IO.Path]::PathSeparator
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $Root
} else {
    "$Root$PathSeparator$env:PYTHONPATH"
}

function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Wants([string]$Name) { return ($Only -eq "" -or $Only -eq $Name) }

$BasePython = $null
$BasePrefix = @()
$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    foreach ($Selector in @("-3.12", "-3.11")) {
        $RawVersion = & $PyLauncher.Source $Selector -c "import sys; print(int((3, 11) <= sys.version_info[:2] <= (3, 12)))" 2>$null
        $VersionOk = if ($RawVersion) { ($RawVersion | Out-String).Trim() } else { "0" }
        if ($LASTEXITCODE -eq 0 -and $VersionOk -eq "1") {
            $BasePython = $PyLauncher.Source
            $BasePrefix = @($Selector)
            break
        }
    }
}
if (-not $BasePython) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $RawVersion = & $PythonCommand.Source -c "import sys; print(int((3, 11) <= sys.version_info[:2] <= (3, 12)))" 2>$null
        $VersionOk = if ($RawVersion) { ($RawVersion | Out-String).Trim() } else { "0" }
        if ($LASTEXITCODE -eq 0 -and $VersionOk -eq "1") {
            $BasePython = $PythonCommand.Source
        }
    }
}
if (-not $BasePython) {
    throw "Python 3.11 ou 3.12 não encontrado. Instale o Python 3.12 x64 pelo python.org e habilite o launcher 'py'."
}

function Get-EnvironmentPython([string]$Name) {
    return Join-Path $script:Envs "$Name\Scripts\python.exe"
}
function Initialize-Environment([string]$Name) {
    $Python = Get-EnvironmentPython $Name
    if (-not (Test-Path $Python)) {
        & $script:BasePython @script:BasePrefix "-m" "venv" (Join-Path $script:Envs $Name) | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente $Name." }
        & $Python -m pip install --quiet --upgrade pip setuptools wheel | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Falha ao preparar $Name." }
    }
    return $Python
}
function Install-PipPackage([string]$Python, [string[]]$PipArgs) {
    & $Python -m pip install --disable-pip-version-check @PipArgs
    if ($LASTEXITCODE -ne 0) { throw "pip falhou para $Python" }
}

Step "Verificando pré-requisitos"
$RawVerCheck = & $BasePython @BasePrefix -c "import sys; print(int(sys.version_info >= (3, 11)))"
$VersionOk = if ($RawVerCheck) { ($RawVerCheck | Out-String).Trim() } else { "0" }
if ($VersionOk -ne "1") { throw "O backend exige Python 3.11 ou mais recente." }
if ($Only -ne "frontend") {
    foreach ($Tool in @("ffmpeg", "ffprobe")) {
        if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
            throw "$Tool não encontrado no PATH. Instale-o antes de continuar."
        }
    }
}
if ((Wants "frontend") -and -not $SkipFrontend) {
    $Tool = "node"
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "$Tool não encontrado no PATH. Instale-o antes de continuar."
    }
}
$HasNvidia = ($null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) -or
    (Test-Path "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe") -or
    (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe")
$TorchIndex = if ($HasNvidia) { "https://download.pytorch.org/whl/cu126" } else { "https://download.pytorch.org/whl/cpu" }
New-Item -ItemType Directory -Force -Path $Envs, (Join-Path $Root "logs"), (Join-Path $Root ".runtime") | Out-Null

if (Wants "backend") {
    Step "Ambiente backend"
    $Python = Initialize-Environment "backend"
    Install-PipPackage $Python @("-r", (Join-Path $Root "requirements\backend.txt"))
}

if (Wants "qwen-tts") {
    Step "Ambiente Qwen3-TTS"
    $Python = Initialize-Environment "qwen-tts"
    Install-PipPackage $Python @("--index-url", $TorchIndex, "torch==2.9.1", "torchaudio==2.9.1")
    Install-PipPackage $Python @("-r", (Join-Path $Root "requirements\qwen-tts.txt"))
}

if (Wants "whisper") {
    Step "Ambiente faster-whisper"
    $Python = Initialize-Environment "whisper"
    Install-PipPackage $Python @("-r", (Join-Path $Root "requirements\whisper.txt"))
}

if ($Only -eq "musetalk" -or ($Only -eq "" -and $WithMuseTalk)) {
    Step "Ambiente MuseTalk (Python 3.10 isolado)"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "O MuseTalk exige uv. Instale com 'winget install astral-sh.uv' e rode novamente."
    }
    & uv python install 3.10
    if ($LASTEXITCODE -ne 0) { throw "uv não instalou o Python 3.10." }
    $MuseDir = Join-Path $Envs "musetalk"
    $MusePython = Join-Path $MuseDir "Scripts\python.exe"
    if (-not (Test-Path $MusePython)) {
        & uv venv --python 3.10 $MuseDir
        if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente MuseTalk." }
    }
    $MuseIndex = if ($HasNvidia) { "https://download.pytorch.org/whl/cu118" } else { "https://download.pytorch.org/whl/cpu" }
    & uv pip install --python $MusePython --index-url $MuseIndex "torch==2.0.1" "torchvision==0.15.2" "torchaudio==2.0.2"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar torch do MuseTalk." }
    & uv pip install --python $MusePython -r (Join-Path $Root "requirements\musetalk.txt")
    if ($LASTEXITCODE -ne 0) { throw "Falha nas dependências do MuseTalk." }
    & uv pip install --python $MusePython pip "setuptools==75.8.0" wheel "mmengine==0.10.4"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao preparar mmengine." }
    $MmcvFindLinks = if ($HasNvidia) { "https://download.openmmlab.com/mmcv/dist/cu118/torch2.0.0/index.html" } else { "https://download.openmmlab.com/mmcv/dist/cpu/torch2.0.0/index.html" }
    & uv pip install --python $MusePython "mmcv==2.0.1" --find-links $MmcvFindLinks
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar mmcv 2.0.1; use o fallback WSL2 documentado." }
    & uv pip install --python $MusePython --no-build-isolation "chumpy==0.70"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar chumpy." }
    & uv pip install --python $MusePython "mmpose==1.1.0" "mmdet==3.1.0"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar mmpose/mmdet." }
    & uv pip install --python $MusePython "setuptools==75.8.0"
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao restaurar setuptools 75.8.0 no MuseTalk."
    }
    & $MusePython -c "from mmpose.apis import inference_topdown; import torch, mmcv, mmdet; print(torch.__version__, mmcv.__version__, mmdet.__version__)"
    if ($LASTEXITCODE -ne 0) { throw "MuseTalk instalou, mas os imports falharam." }
}

if ((Wants "frontend") -and -not $SkipFrontend) {
    Step "Interface web"
    Push-Location (Join-Path $Root "apps\web")
    try {
        if (Get-Command pnpm -ErrorAction SilentlyContinue) { & pnpm install }
        elseif (Get-Command npm -ErrorAction SilentlyContinue) { & npm install }
        else { throw "pnpm/npm não encontrado." }
        if ($LASTEXITCODE -ne 0) { throw "Instalação do frontend falhou." }
    } finally { Pop-Location }
}

Step "Preparando diretórios e diagnóstico"
$Backend = Get-EnvironmentPython "backend"
if (Test-Path $Backend) {
    & $Backend -c "from core.storage.paths import get_paths; from core.hardware.detect import detect,save_report; p=get_paths(); p.ensure_runtime_dirs(); save_report(detect(p.root),p.hardware_profile_file)"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao detectar o hardware e preparar os diretórios." }
    # $LASTEXITCODE ignorado de propósito: o doctor devolve != 0 sempre que há
    # pendências (ex.: pesos ainda não baixados), o que é o estado normal ao
    # fim de uma instalação limpa. Ele é informativo, não uma condição de erro.
    & $Backend -m apps.cli.main doctor
} else {
    Write-Warning "Backend ainda não instalado; diagnóstico final pulado."
}

if (-not $NoModels -and $Only -eq "") {
    Write-Host "`nNenhum peso é baixado automaticamente."
    $Download = [bool]$Yes
    if (-not $Yes) {
        $Answer = Read-Host "Baixar agora os modelos essenciais? [s/N]"
        $Download = $Answer -match "^[sSyY]"
    }
    if ($Download -and (Test-Path $Backend)) { & $Backend (Join-Path $Root "scripts\models.py") install --essential }
    else { Write-Host ".\scripts\models.ps1 install --essential" }
}

Write-Host "`nInstalação concluída. Inicie com: .\start.ps1" -ForegroundColor Green
