$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    & py -3.13 -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "PokemonGO整理助手" `
        --distpath (Join-Path $projectRoot "release") `
        --workpath (Join-Path $projectRoot "build\gui") `
        --specpath (Join-Path $projectRoot "build\gui") `
        (Join-Path $projectRoot "launcher.py")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

