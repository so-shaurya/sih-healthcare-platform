$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

if (-not (Test-Path '.runtime')) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Python launcher (py) was not found. Install Python 3.11 or later, then rerun this script.'
    }
    py -3 -m venv .runtime
}
& .\.runtime\Scripts\python.exe -m pip install -r requirements.txt
& .\.runtime\Scripts\python.exe app.py
