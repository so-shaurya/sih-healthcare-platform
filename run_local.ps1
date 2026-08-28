$ErrorActionPreference = 'Stop'
$project = Join-Path $PSScriptRoot 'sih_complete_architecture'
& (Join-Path $project 'run_local.ps1')
