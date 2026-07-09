# smartpipe one-line installer (Windows PowerShell):
#
#   powershell -ExecutionPolicy Bypass -c "irm https://prabal-rje.github.io/smartpipe/install.ps1 | iex"
#
# Bootstraps uv (astral.sh/uv) when missing, then installs the smartpipe-cli
# tool. $env:SMARTPIPE_VERSION = "X.Y.Z" pins the version.
# Rerunning is safe: an existing install is upgraded, never broken.
$ErrorActionPreference = "Stop"

$existing = Get-Command smartpipe -ErrorAction SilentlyContinue
$uvManaged = $false
if ($existing -and (Get-Command uv -ErrorAction SilentlyContinue)) {
    $uvManaged = [bool]((uv tool list) -match '^smartpipe-cli ')
}

if ($existing -and -not $uvManaged) {
    Write-Host "smartpipe already installed via $($existing.Source); use your installer's upgrade"
} else {
    if ($uvManaged) {
        Write-Host "smartpipe already installed - upgrading with uv"
        # a plain 'uv tool install' refuses to touch an installed tool
        if ($env:SMARTPIPE_VERSION) { uv tool install --force "smartpipe-cli==$($env:SMARTPIPE_VERSION)" }
        else { uv tool upgrade smartpipe-cli }
    } else {
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
    }

    Write-Host ""
    if (Get-Command smartpipe -ErrorAction SilentlyContinue) {
        Write-Host "smartpipe installed:"
        smartpipe --version
        Write-Host "get started: smartpipe config"
    } else {
        Write-Host "installed, but 'smartpipe' is not on PATH in this session."
        Write-Host "run 'uv tool update-shell', open a new terminal, then check: smartpipe --version"
    }
}
