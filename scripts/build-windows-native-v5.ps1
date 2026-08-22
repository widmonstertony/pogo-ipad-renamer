$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $projectRoot "src"
$releaseRoot = Join-Path $projectRoot "release"
$buildRoot = Join-Path $projectRoot "build\windows-native-v5"

Push-Location $projectRoot
try {
    & py -3.13 -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --paths $sourceRoot `
        --name "PokemonGO-Renamer" `
        --distpath $releaseRoot `
        --workpath $buildRoot `
        --specpath $buildRoot `
        (Join-Path $projectRoot "launcher_native_v5.py")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

