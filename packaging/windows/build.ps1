param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dist = Join-Path $root 'dist'

Push-Location -LiteralPath $root
try {
    $version = & $Python -c "from testmyapp import __version__; print(__version__)"
    $build = Join-Path $root "build\windows-v$version"
    New-Item -ItemType Directory -Path $build -Force | Out-Null
    $venv = Join-Path $build 'venv'
    if (-not (Test-Path -LiteralPath "$venv\Scripts\python.exe")) {
        & $Python -m venv $venv
    }
    $buildPython = "$venv\Scripts\python.exe"
    & $buildPython -c "import platform,sys; assert sys.platform == 'win32' and platform.machine() == 'AMD64', 'Windows x64 Python required'"
    & $buildPython -m pip --disable-pip-version-check install '.[web,desktop]' 'pyinstaller==6.22.3'

    $licenses = Join-Path $build 'licenses'
    @'
from importlib.metadata import distributions
from pathlib import Path
from shutil import copyfile
import sys

destination = Path(sys.argv[1])
for package in distributions():
    for item in package.files or ():
        if not item.parts[0].endswith(".dist-info"):
            continue
        if "licenses" not in item.parts and not item.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
            continue
        source = package.locate_file(item)
        if source.is_file():
            target = destination / package.metadata["Name"] / Path(*item.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, target)
'@ | & $buildPython - $licenses

    $arguments = @(
        '--noconfirm', '--clean', '--onefile', '--console', '--noupx',
        '--name', "TestMyApp-v$version-windows-x64",
        '--paths', $root, '--distpath', $dist, '--workpath', "$build\pyinstaller", '--specpath', $build,
        '--collect-all', 'testmyapp', '--collect-all', 'playwright',
        '--collect-submodules', 'uvicorn', '--collect-submodules', 'websockets',
        '--collect-data', 'textual', '--copy-metadata', 'testmyapp', '--add-data', "$licenses;licenses",
        (Join-Path $PSScriptRoot 'launcher.py')
    )
    & $buildPython -m PyInstaller @arguments
} finally {
    Pop-Location
}
