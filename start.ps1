Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
    & '.\.venv\Scripts\python.exe' -m uvicorn app:app --host 127.0.0.1 --port 8765
} else {
    py -m uvicorn app:app --host 127.0.0.1 --port 8765
}
