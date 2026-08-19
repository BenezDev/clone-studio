param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DoctorArgs)
$Root = Split-Path -Parent $PSScriptRoot
$env:CLONE_STUDIO_ROOT = $Root
$env:PYTHONUTF8 = "1"
$PathSeparator = [IO.Path]::PathSeparator
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) { $Root } else { "$Root$PathSeparator$env:PYTHONPATH" }
$Python = Join-Path $Root ".envs\backend\Scripts\python.exe"
# `python -m apps.cli.main` só resolve o pacote com a raiz no sys.path,
# e o `-m` do Python coloca lá apenas o diretório atual. Sem isto o
# wrapper falha com ModuleNotFoundError quando chamado de outra pasta.
$env:PYTHONPATH = (@($Root, $env:PYTHONPATH) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
if (-not (Test-Path $Python)) { throw "Rode install.ps1 primeiro." }
& $Python -m apps.cli.main doctor @DoctorArgs
exit $LASTEXITCODE
