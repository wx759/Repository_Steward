param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$HostAddress = "127.0.0.1"
$BackendPort = 8002
$FrontendPort = 7788
$Status = $false
$Stop = $false
$Restart = $false

for ($index = 0; $index -lt $Arguments.Count; $index++) {
    $argument = $Arguments[$index]
    switch -Regex ($argument) {
        '^--?restart$' { $Restart = $true; continue }
        '^--?status$' { $Status = $true; continue }
        '^--?stop$' { $Stop = $true; continue }
        '^--?host-address$' {
            if ($index + 1 -ge $Arguments.Count) { throw "$argument requires a value." }
            $index++
            $HostAddress = $Arguments[$index]
            continue
        }
        '^--?backend-port$' {
            if ($index + 1 -ge $Arguments.Count) { throw "$argument requires a value." }
            $index++
            $BackendPort = [int]$Arguments[$index]
            continue
        }
        '^--?frontend-port$' {
            if ($index + 1 -ge $Arguments.Count) { throw "$argument requires a value." }
            $index++
            $FrontendPort = [int]$Arguments[$index]
            continue
        }
        default { throw "Unknown argument: $argument" }
    }
}

if (($Status -and $Stop) -or ($Status -and $Restart) -or ($Stop -and $Restart)) {
    throw "Use only one of --status, --stop, or --restart."
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $Root ".repository_steward\dev_services"
$PidFile = Join-Path $RuntimeDir "pids.json"
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$PythonExe = Join-Path $BackendDir ".venv\Scripts\python.exe"
$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$CmdExe = (Get-Command cmd.exe -ErrorAction Stop).Source

function Read-ServiceState {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $null
    }
    return Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
}

function Test-ServiceProcess {
    param([int]$ProcessId)

    try {
        Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Stop-ServiceProcessTree {
    param([int]$ProcessId)

    if (-not (Test-ServiceProcess -ProcessId $ProcessId)) {
        return
    }

    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Test-ServiceProcess -ProcessId $ProcessId)) {
        Stop-Process -Id $ProcessId -Force
    }
}

function Get-ListeningProcessIdsByPort {
    param([int[]]$Ports)

    $portSet = @{}
    foreach ($port in $Ports) {
        $portSet[[string]$port] = $true
    }

    $processIds = @()
    foreach ($line in (& netstat.exe -ano)) {
        if ($line -notmatch '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
            continue
        }
        if ($portSet.ContainsKey($Matches[1])) {
            $processIds += [int]$Matches[2]
        }
    }
    return @($processIds | Select-Object -Unique)
}

function Show-ServiceStatus {
    $state = Read-ServiceState
    if ($null -eq $state) {
        Write-Host "No saved service state."
        return
    }

    foreach ($service in $state.services) {
        $alive = Test-ServiceProcess -ProcessId ([int]$service.pid)
        $statusText = if ($alive) { "running" } else { "stopped" }
        Write-Host ("{0}: {1} pid={2}" -f $service.name, $statusText, $service.pid)
        Write-Host ("  stdout: {0}" -f $service.stdout)
        Write-Host ("  stderr: {0}" -f $service.stderr)
    }
    Write-Host ("Frontend URL: {0}" -f $state.frontend_url)
    Write-Host ("Backend URL:  {0}" -f $state.backend_url)
}

function Stop-Services {
    $state = Read-ServiceState
    if ($null -eq $state) {
        Write-Host "No saved service state."
        return
    }

    foreach ($service in $state.services) {
        $processId = [int]$service.pid
        Stop-ServiceProcessTree -ProcessId $processId
        Write-Host ("Stopped {0} pid={1}" -f $service.name, $processId)
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Start-ServiceProcess {
    param(
        [string]$Name,
        [string]$WorkingDirectory,
        [string]$FilePath,
        [string[]]$Arguments
    )

    $stdout = Join-Path $RuntimeDir "$Name.stdout.log"
    $stderr = Join-Path $RuntimeDir "$Name.stderr.log"
    $launcher = Join-Path $RuntimeDir "$Name.ps1"
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue

    $argumentList = @(
        foreach ($argument in $Arguments) {
            "'{0}'" -f ([string]$argument -replace "'", "''")
        }
    ) -join ", "

    $scriptLines = @(
        '$ErrorActionPreference = "Stop"',
        ("Set-Location -LiteralPath '{0}'" -f ($WorkingDirectory -replace "'", "''")),
        ("& '{0}' @({1})" -f ($FilePath -replace "'", "''"), $argumentList)
    )
    Set-Content -LiteralPath $launcher -Value $scriptLines -Encoding UTF8

    $process = Start-Process `
        -FilePath $PowerShellExe `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher) `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    return [ordered]@{
        name = $Name
        pid = $process.Id
        stdout = $stdout
        stderr = $stderr
    }
}

function Wait-ServicePort {
    param(
        [string]$Name,
        [int]$ProcessId,
        [int]$Port,
        [string]$StdoutPath,
        [string]$StderrPath,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-ServiceProcess -ProcessId $ProcessId)) {
            Write-Host ("{0} exited during startup." -f $Name)
            if (Test-Path -LiteralPath $StdoutPath) {
                Get-Content -LiteralPath $StdoutPath | Write-Host
            }
            if (Test-Path -LiteralPath $StderrPath) {
                Get-Content -LiteralPath $StderrPath | Write-Host
            }
            return $false
        }

        if (@(Get-ListeningProcessIdsByPort -Ports @($Port)).Count -gt 0) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }

    Write-Host ("{0} did not listen on port {1} within {2} seconds." -f $Name, $Port, $TimeoutSeconds)
    if (Test-Path -LiteralPath $StdoutPath) {
        Get-Content -LiteralPath $StdoutPath | Write-Host
    }
    if (Test-Path -LiteralPath $StderrPath) {
        Get-Content -LiteralPath $StderrPath | Write-Host
    }
    return $false
}

if ($Status) {
    Show-ServiceStatus
    exit 0
}

if ($Stop) {
    Stop-Services
    exit 0
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Backend Python was not found: $PythonExe"
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "package.json"))) {
    throw "Frontend package.json was not found."
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "node_modules"))) {
    throw "Frontend dependencies are missing. Run npm.cmd install in the frontend directory."
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

$existingState = Read-ServiceState
if ($null -ne $existingState) {
    $running = @(
        foreach ($service in $existingState.services) {
            if (Test-ServiceProcess -ProcessId ([int]$service.pid)) {
                $service
            }
        }
    )
    if ($running.Count -gt 0) {
        if (-not $Restart) {
            Write-Host "Services already appear to be running. Use -Status, -Stop, or -Restart."
            Show-ServiceStatus
            exit 1
        }
        Stop-Services
        Start-Sleep -Milliseconds 500
    }
    else {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

$managedPorts = @($BackendPort, $FrontendPort)
$busyProcessIds = @(Get-ListeningProcessIdsByPort -Ports $managedPorts)
if ($busyProcessIds.Count -gt 0) {
    Write-Host ("Configured ports are already in use by pid(s): {0}." -f ($busyProcessIds -join ", "))
    Write-Host "Stop those services first. If Docker owns port 7788, run: docker compose down"
    exit 1
}

$backendUrl = "http://{0}:{1}" -f $HostAddress, $BackendPort
$frontendUrl = "http://{0}:{1}" -f $HostAddress, $FrontendPort

$backend = Start-ServiceProcess `
    -Name "backend" `
    -WorkingDirectory $BackendDir `
    -FilePath $PythonExe `
    -Arguments @(
        "-m", "uvicorn", "app:app",
        "--host", $HostAddress,
        "--port", [string]$BackendPort,
        "--reload"
    )

if (-not (Wait-ServicePort `
    -Name "backend" `
    -ProcessId ([int]$backend.pid) `
    -Port $BackendPort `
    -StdoutPath ([string]$backend.stdout) `
    -StderrPath ([string]$backend.stderr))) {
    Stop-ServiceProcessTree -ProcessId ([int]$backend.pid)
    exit 1
}

$frontend = Start-ServiceProcess `
    -Name "frontend" `
    -WorkingDirectory $FrontendDir `
    -FilePath $CmdExe `
    -Arguments @("/d", "/c", "npm.cmd", "run", "dev")

if (-not (Wait-ServicePort `
    -Name "frontend" `
    -ProcessId ([int]$frontend.pid) `
    -Port $FrontendPort `
    -StdoutPath ([string]$frontend.stdout) `
    -StderrPath ([string]$frontend.stderr))) {
    Stop-ServiceProcessTree -ProcessId ([int]$frontend.pid)
    Stop-ServiceProcessTree -ProcessId ([int]$backend.pid)
    exit 1
}

$state = [ordered]@{
    started_at = (Get-Date).ToString("o")
    frontend_url = $frontendUrl
    backend_url = $backendUrl
    services = @($backend, $frontend)
}
$state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $PidFile -Encoding UTF8

Write-Host "Started services:"
Show-ServiceStatus
