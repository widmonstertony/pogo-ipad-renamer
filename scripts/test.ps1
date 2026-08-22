$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot 'src'
Push-Location $projectRoot
try {
    & py -3.13 -m unittest discover -s tests -v
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

