# Run from any working directory; Task Scheduler can invoke this script.
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectDirectory
try {
    & $pythonExecutable -m jobradar collect
    $collectionExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $collectionExitCode
