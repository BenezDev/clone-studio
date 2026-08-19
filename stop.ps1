$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$env:CLONE_STUDIO_ROOT = $Root
$env:PYTHONUTF8 = "1"
$PathSeparator = [IO.Path]::PathSeparator
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $Root
} else {
    "$Root$PathSeparator$env:PYTHONPATH"
}
$Python = Join-Path $Root ".envs\backend\Scripts\python.exe"
# `python -m apps.cli.main` só resolve o pacote com a raiz no sys.path,
# e o `-m` do Python coloca lá apenas o diretório atual. Sem isto o
# wrapper falha com ModuleNotFoundError quando chamado de outra pasta.
$env:PYTHONPATH = (@($Root, $env:PYTHONPATH) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
if (-not (Test-Path $Python)) {
    Write-Host "Ambiente backend ausente; nada para encerrar."
    exit 0
}
& $Python -m scripts.launch --stop
exit $LASTEXITCODE
