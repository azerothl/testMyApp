param(
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$exe = Get-Item -LiteralPath $Executable
$results = (New-Item -ItemType Directory -Path $OutputDirectory -Force).FullName
$resultFile = Join-Path $results 'result.json'
if (Test-Path -LiteralPath $resultFile) { throw 'Use an output directory with no previous result.json.' }

$sandboxId = [guid]::NewGuid()
$staging = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP "testmyapp-sandbox-$sandboxId")).FullName
Copy-Item -LiteralPath $exe.FullName,(Join-Path $PSScriptRoot 'run_windows_portable.ps1') -Destination $staging

# Run the test as inline commands under Windows Sandbox's default execution policy.
$guestCommand = @'
$script = [IO.File]::ReadAllText('C:\TestMyAppInput\run_windows_portable.ps1')
$source = Get-ChildItem -LiteralPath 'C:\TestMyAppInput' -Filter '*.exe' -File
$exe = Join-Path $env:TEMP $source.Name
Copy-Item -LiteralPath $source.FullName -Destination $exe
& ([ScriptBlock]::Create($script)) -Executable $exe -OutputDirectory 'C:\TestMyAppResults' -CleanWindows
'@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($guestCommand))
$stagingPath = [Security.SecurityElement]::Escape($staging)
$resultsPath = [Security.SecurityElement]::Escape($results)
$config = @"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Enable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$stagingPath</HostFolder>
      <SandboxFolder>C:\TestMyAppInput</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>$resultsPath</HostFolder>
      <SandboxFolder>C:\TestMyAppResults</SandboxFolder>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -EncodedCommand $encoded</Command>
  </LogonCommand>
</Configuration>
"@
$started = $false
try {
    & wsb start --id "$sandboxId" --config $config
    if ($LASTEXITCODE -ne 0) { throw 'Windows Sandbox did not start.' }
    $started = $true
    $wsb = (Get-Command wsb.exe -ErrorAction Stop).Source
    Start-Process -FilePath $wsb -ArgumentList @('connect', '--id', "$sandboxId") -WindowStyle Hidden
    $deadline = (Get-Date).AddMinutes(15)
    while (-not (Test-Path -LiteralPath $resultFile)) {
        if ((Get-Date) -gt $deadline) { throw 'Windows Sandbox did not return a test result.' }
        Start-Sleep -Seconds 3
    }
    $result = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
    $result | ConvertTo-Json -Depth 5
    if ($result.status -ne 'passed' -or -not $result.cleanWindows -or
        $result.sha256 -ne (Get-FileHash -LiteralPath $exe.FullName).Hash) {
        throw 'The executable did not pass its Windows Sandbox test.'
    }
} finally {
    if ($started) { & wsb stop --id "$sandboxId" }
    foreach ($file in $exe.Name,'run_windows_portable.ps1') {
        Remove-Item -LiteralPath (Join-Path $staging $file)
    }
    Remove-Item -LiteralPath $staging
}
