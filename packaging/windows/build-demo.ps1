$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:windir 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$assets = (Resolve-Path (Join-Path $PSScriptRoot '..\..\testmyapp\assets\desktop')).Path
& $compiler /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Drawing.dll /reference:System.Windows.Forms.dll "/out:$assets\RecetteWindows.exe" "$assets\RecetteWindows.cs"
if ($LASTEXITCODE -ne 0) { throw "La compilation de la démo WinForms a échoué (code $LASTEXITCODE)." }
