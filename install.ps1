# dctl installer for Windows (PowerShell). No admin rights needed.
#
#   irm https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/dctl/main/install.ps1 | iex
#
# Installs with pipx if you have it, otherwise into its own virtualenv in
# %LOCALAPPDATA%\dctl and adds a small bin folder (only dctl-ui / dctl) to your user PATH.
# On Windows the web terminal is not available; everything else works. For the full
# experience (web terminal, bash CLI) install inside WSL with install.sh instead.
$ErrorActionPreference = 'Stop'

$Repo = 'https://github.com/YOUR-GITHUB-USERNAME/dctl'
$Src  = if ($env:DCTL_SRC) { $env:DCTL_SRC } else { "$Repo/archive/refs/heads/main.tar.gz" }

function Say($m) { Write-Host ">> $m" -ForegroundColor Magenta }

$py = $null; $pyArgs = @()
foreach ($c in @(@('py', '-3'), @('python'), @('python3'))) {
    $exe = $c[0]; $pre = @($c | Select-Object -Skip 1)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        & $exe @pre -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $exe; $pyArgs = $pre; break }
    }
}
if (-not $py) { throw 'Python 3.8 or newer is needed: https://www.python.org/downloads/ (tick "Add to PATH")' }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw 'The OpenSSH client is needed: Settings > Apps > Optional features > OpenSSH Client'
}

if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Say 'installing with pipx'
    pipx install --force $Src
} else {
    $Home_ = Join-Path $env:LOCALAPPDATA 'dctl'
    $Venv  = Join-Path $Home_ 'venv'
    $Bin   = Join-Path $Home_ 'bin'
    Say "installing into $Home_"
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    & $py @pyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'could not create a virtualenv' }
    $vpy = Join-Path $Venv 'Scripts\python.exe'
    & $vpy -m pip install --quiet --upgrade pip 2>$null
    & $vpy -m pip install --quiet $Src
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
    New-Item -ItemType Directory -Force $Bin | Out-Null
    foreach ($n in 'dctl', 'dctl-ui') {
        Set-Content -Encoding ASCII (Join-Path $Bin "$n.cmd") "@`"$Venv\Scripts\$n.exe`" %*"
    }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (($userPath -split ';') -contains $Bin)) {
        [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $Bin), 'User')
        Say "added $Bin to your PATH (open a new terminal)"
    }
}

Say 'done. Start the dashboard with:  dctl-ui'
