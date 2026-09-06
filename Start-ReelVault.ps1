# ReelVault launcher
# Starts the app + AI server, opens your browser. No terminal knowledge needed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
    Write-Host "First run: setting up Python environment (one time)..." -ForegroundColor Cyan
    python -m venv .venv
    & "$root\.venv\Scripts\python.exe" -m pip install --quiet fastapi "uvicorn[standard]" pydantic pydantic-settings httpx yt-dlp rapidocr-onnxruntime fastembed pillow numpy pytest pytest-timeout python-multipart python-dateutil openai-whisper
}

Write-Host ""
Write-Host "  ██████╗ ███████╗███████╗██╗██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗" -ForegroundColor Blue
Write-Host "  ██╔══██╗██╔════╝██╔════╝██║██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝" -ForegroundColor Blue
Write-Host "  ██████╔╝█████╗  █████╗  ██║██║   ██║███████║██║   ██║██║     ██║" -ForegroundColor Blue
Write-Host "  ██╔══██╗██╔══╝  ██╔══╝  ██║╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║" -ForegroundColor Blue
Write-Host "  ██║  ██║███████╗██║     ██║ ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║" -ForegroundColor Blue
Write-Host "  ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝  ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝" -ForegroundColor Blue
Write-Host ""
Write-Host "  Your Reels -> searchable knowledge. 100% local & private." -ForegroundColor Gray
Write-Host ""

# open browser once server responds
Start-Job -ScriptBlock {
    param($root)
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8756/healthz" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { Start-Process "http://127.0.0.1:8756"; break }
        } catch { Start-Sleep -Milliseconds 800 }
    }
} -ArgumentList $root | Out-Null

& "$root\.venv\Scripts\python.exe" -m uvicorn app.api.main:app --host 0.0.0.0 --port 8756
