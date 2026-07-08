# smartpipe one-line installer (Windows PowerShell):
#
#   powershell -ExecutionPolicy Bypass -c "irm https://prabal-rje.github.io/smartpipe/install.ps1 | iex"
#
# Bootstraps uv (astral.sh/uv) when missing, then installs the smartpipe-cli
# tool. $env:SMARTPIPE_VERSION = "X.Y.Z" pins the version.
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "bootstrapping uv (https://astral.sh/uv)"
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # uv lands in %USERPROFILE%\.local\bin; make it reachable for THIS run
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

$spec = "smartpipe-cli"
if ($env:SMARTPIPE_VERSION) { $spec = "smartpipe-cli==$($env:SMARTPIPE_VERSION)" }
Write-Host "installing with uv: uv tool install $spec"
uv tool install $spec

Write-Host ""
if (Get-Command smartpipe -ErrorAction SilentlyContinue) {
    Write-Host "smartpipe installed:"
    smartpipe --version
    Write-Host "get started: smartpipe config"
} else {
    Write-Host "installed, but 'smartpipe' is not on PATH in this session."
    Write-Host "run 'uv tool update-shell', open a new terminal, then check: smartpipe --version"
}
