param(
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [switch]$CleanWindows
)

$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false)
$exe = (Resolve-Path -LiteralPath $Executable).Path
$results = (New-Item -ItemType Directory -Path $OutputDirectory -Force).FullName
$workspace = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP "testmyapp-portable-$([guid]::NewGuid())")).FullName
$policy = Get-ExecutionPolicy
$policies = Get-ExecutionPolicy -List | ConvertTo-Json -Compress
$checks = [ordered]@{ status = 'failed'; cleanWindows = [bool]$CleanWindows; policyBefore = "$policy" }
$shop = $null

function Start-PortableProcess($arguments, $directory) {
    $info = [Diagnostics.ProcessStartInfo]::new($exe, $arguments)
    $info.WorkingDirectory = $directory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $utf8
    $info.StandardErrorEncoding = $utf8
    [Diagnostics.Process]::Start($info)
}

function Stop-PortableProcess($process) {
    if ($process -and -not $process.HasExited) {
        & "$env:windir\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null
    }
}

function Invoke-Portable($label, $arguments, $directory) {
    Write-Host "TestMyApp $arguments"
    $process = Start-PortableProcess $arguments $directory
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(300000)) { throw "Timeout: $label" }
        [IO.File]::WriteAllText((Join-Path $results "$label.stdout.log"), $stdout.GetAwaiter().GetResult(), $utf8)
        [IO.File]::WriteAllText((Join-Path $results "$label.stderr.log"), $stderr.GetAwaiter().GetResult(), $utf8)
        return $process.ExitCode
    } finally {
        Stop-PortableProcess $process
        $process.Dispose()
    }
}

function Export-Campaign($name, $campaign, $directory) {
    $exitCode = Invoke-Portable "$name-run" "run -c $campaign --mode headless" $directory
    $run = Get-ChildItem -LiteralPath (Join-Path $directory 'runs') -Directory | Select-Object -First 1
    if (-not $run) { throw "No run was produced: $name (exit $exitCode)" }
    $report = Join-Path $run.FullName 'report'
    $eventLog = Join-Path $run.FullName 'events.jsonl'
    $before = (Get-FileHash -LiteralPath $eventLog).Hash
    $destination = (New-Item -ItemType Directory -Path (Join-Path $results $name) -Force).FullName
    Copy-Item -LiteralPath "$report\report.json","$report\report.html",$eventLog -Destination $destination
    if ((Invoke-Portable "$name-pdf" "report runs\$($run.Name) --format pdf" $directory) -ne 0) {
        throw "PDF export failed: $name"
    }
    $pdf = "$report\report.pdf"
    Copy-Item -LiteralPath $pdf -Destination $destination
    $data = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($pdf))
    if (-not $data.StartsWith('%PDF-') -or $data -notmatch '%%EOF\s*$' -or
        $data -notmatch '/StructTreeRoot\b' -or $data -notmatch '/Subtype\s*/Image\b' -or
        $data -notmatch '/Subtype\s*/Link\b' -or $before -ne (Get-FileHash -LiteralPath $eventLog).Hash) {
        throw "PDF integrity or evidence check failed: $name"
    }
    [pscustomobject]@{
        exitCode = $exitCode
        report = Get-Content -LiteralPath "$report\report.json" -Encoding utf8 -Raw | ConvertFrom-Json
        events = @(Get-Content -LiteralPath $eventLog -Encoding utf8 | ForEach-Object { $_ | ConvertFrom-Json })
        pdfBytes = (Get-Item -LiteralPath $pdf).Length
    }
}

try {
    if (-not [Environment]::UserInteractive -or [Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
        throw 'An interactive Windows desktop is required for the WinForms campaign.'
    }
    if ($CleanWindows) {
        $python = @(Get-Command python.exe,python3.exe,py.exe -All -ErrorAction SilentlyContinue |
            Where-Object Source -NotMatch '\\Microsoft\\WindowsApps\\')
        $registry = @('HKLM:\SOFTWARE\Python', 'HKLM:\SOFTWARE\WOW6432Node\Python', 'HKCU:\SOFTWARE\Python' |
            Where-Object { Test-Path -LiteralPath $_ })
        $installations = @(Get-Item -Path 'C:\Python*','C:\Program Files\Python*',
            'C:\Program Files (x86)\Python*',"$env:LOCALAPPDATA\Programs\Python\*",
            "$env:USERPROFILE\Anaconda*","$env:USERPROFILE\Miniconda*" -ErrorAction SilentlyContinue)
        if ($python.Count + $registry.Count + $installations.Count -ne 0 -or
            $policy -ne 'Restricted' -or (Test-Path Env:\PSExecutionPolicyPreference)) {
            throw 'Expected Windows Sandbox with no Python installation and the default Restricted policy.'
        }
    }

    # Official hosted runners have additional tools. Only Windows is available via PATH during the test.
    $env:Path = "$env:windir\System32;$env:windir"
    Remove-Item Env:\PYTHONHOME,Env:\PYTHONPATH,Env:\PYTHONEXECUTABLE -ErrorAction SilentlyContinue
    if (Get-Command python.exe,python3.exe,py.exe -ErrorAction SilentlyContinue) { throw 'Python is on PATH.' }
    $checks.pythonOnPath = $false
    $checks.sha256 = (Get-FileHash -LiteralPath $exe).Hash
    if ((Invoke-Portable version '--version' $workspace) -ne 0) { throw 'The portable executable did not start.' }
    if (Test-Path Env:\PLAYWRIGHT_BROWSERS_PATH) { throw 'Expected the executable default browser location.' }
    if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'ms-playwright')) { throw 'Expected an empty browser cache.' }
    if ((Invoke-Portable chromium 'install-browser chromium --only-shell' $workspace) -ne 0) {
        throw 'The portable executable could not install Chromium.'
    }

    $webProject = (New-Item -ItemType Directory -Path (Join-Path $workspace 'web')).FullName
    $desktopProject = (New-Item -ItemType Directory -Path (Join-Path $workspace 'desktop')).FullName
    if ((Invoke-Portable web-init 'init' $webProject) -ne 0 -or
        (Invoke-Portable desktop-init 'init' $desktopProject) -ne 0) { throw 'Demo project initialization failed.' }
    if ((Invoke-Portable desktop-target 'target check demo-windows' $desktopProject) -ne 0 -or
        (Get-Content "$results\desktop-target.stdout.log" -Raw) -notmatch 'RecetteWindows\.exe') {
        throw 'The Windows demo did not resolve its bundled WinForms executable.'
    }

    $shop = Start-PortableProcess 'demo --fixed --port 7419' $webProject
    $shopOut = $shop.StandardOutput.ReadToEndAsync()
    $shopErr = $shop.StandardError.ReadToEndAsync()
    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    while (-not $ready) {
        if ($shop.HasExited -or (Get-Date) -gt $deadline) { throw 'The demo shop did not start.' }
        try {
            $webResponse = Invoke-WebRequest 'http://127.0.0.1:7419/' -UseBasicParsing -TimeoutSec 2
            $ready = $webResponse.StatusCode -eq 200 -and $webResponse.Content -match 'Rechercher un produit'
        } catch { Start-Sleep -Milliseconds 500 }
    }
    $web = Export-Campaign web recette-web-corrigee $webProject
    Stop-PortableProcess $shop
    $desktop = Export-Campaign desktop recette-windows $desktopProject
    $checks.web = @{ verdict = $web.report.status; pdfBytes = $web.pdfBytes }
    $checks.desktop = @{ verdict = $desktop.report.status; pdfBytes = $desktop.pdfBytes }

    $webSessions = @($web.report.sessions | Where-Object { $_.outcome -eq 'pass' -and $_.steps_passed -eq 3 })
    if ($web.exitCode -ne 0 -or $web.report.status -ne 'pass' -or $web.report.campaign.target_type -ne 'web' -or
        (($webSessions.persona | Sort-Object) -join ',') -ne 'eric,karim') {
        throw 'The Web demo did not validate both purchase journeys.'
    }
    $action = $desktop.report.sessions | Where-Object scenario -eq 'windows-action'
    $freeze = $desktop.report.sessions | Where-Object scenario -eq 'windows-gel'
    $crash = $desktop.report.sessions | Where-Object scenario -eq 'windows-crash'
    $freezeMs = ($freeze.freezes_ms | Measure-Object -Maximum).Maximum
    $exitEvent = $desktop.events | Where-Object { $_.type -eq 'target.crash' -and $_.exit_code -eq 23 }
    if ($desktop.exitCode -ne 1 -or $desktop.report.status -ne 'error' -or
        $desktop.report.campaign.target_type -ne 'desktop' -or $action.outcome -ne 'pass' -or
        $freeze.outcome -ne 'pass' -or $freezeMs -lt 1000 -or -not $crash.crashed -or -not $exitEvent) {
        throw 'The Windows demo did not validate the action, recovery and intentional process exit.'
    }
    $checks.desktop.freezeMs = $freezeMs
    $checks.desktop.intentionalCrash = 23
    if ((Get-ExecutionPolicy -List | ConvertTo-Json -Compress) -ne $policies) {
        throw 'PowerShell execution policy changed during validation.'
    }
    $checks.status = 'passed'
} catch {
    $checks.error = $_.ToString()
    throw
} finally {
    if ($shop) {
        Stop-PortableProcess $shop
        $shop.WaitForExit()
        [IO.File]::WriteAllText((Join-Path $results 'shop.stdout.log'), $shopOut.GetAwaiter().GetResult(), $utf8)
        [IO.File]::WriteAllText((Join-Path $results 'shop.stderr.log'), $shopErr.GetAwaiter().GetResult(), $utf8)
        $shop.Dispose()
    }
    $checks.policyAfter = [string](Get-ExecutionPolicy)
    $pending = Join-Path $results 'result.pending.json'
    [IO.File]::WriteAllText($pending, ($checks | ConvertTo-Json -Depth 5), $utf8)
    Move-Item -LiteralPath $pending -Destination (Join-Path $results 'result.json') -Force
}
