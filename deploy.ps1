$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Write-Step($message) {
    Write-Host "[AIProxy] $message" -ForegroundColor Cyan
}

function Find-Python {
    $candidates = @("python", "py")
    foreach ($cmd in $candidates) {
        try {
            $version = & $cmd --version 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $cmd
            }
        } catch {
        }
    }
    throw "Python 3 is not installed or not available in PATH."
}

$PythonCmd = Find-Python
$VenvPath = Join-Path $ProjectRoot ".venv"
$ActivateScript = Join-Path $VenvPath "Scripts\Activate.ps1"

if (-not (Test-Path $VenvPath)) {
    Write-Step "Creating virtual environment..."
    & $PythonCmd -m venv $VenvPath
}

Write-Step "Activating virtual environment..."
. $ActivateScript

Write-Step "Upgrading pip..."
python -m pip install --upgrade pip

Write-Step "Installing dependencies..."
python -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Write-Step "Creating .env from .env.example..."
    Copy-Item ".env.example" ".env"
}

Write-Step "Starting AIProxy services..."
python main.py
