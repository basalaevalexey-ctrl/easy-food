Set-Location -Path $PSScriptRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe -m app.bot
} else {
    python -m app.bot
}
