param([int]$RootProcessId, [string]$StopFile, [string]$OutputFile, [switch]$Once)
$ErrorActionPreference = 'Stop'
$tracked = [System.Collections.Generic.HashSet[int]]::new()
if ($RootProcessId -gt 0) { [void]$tracked.Add($RootProcessId) }
while (-not (Test-Path -LiteralPath $StopFile)) {
    try {
        $processes = @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name)
        do {
            $added = $false
            foreach ($item in $processes) {
                if ($tracked.Contains([int]$item.ParentProcessId) -and $tracked.Add([int]$item.ProcessId)) { $added = $true }
            }
        } while ($added)
        $connections = @(Get-NetTCPConnection)
        $tunnelIds = @($connections | Where-Object { $_.LocalPort -eq 11435 -and $_.State -eq 'Listen' } | ForEach-Object { [int]$_.OwningProcess })
        do {
            $added = $false
            foreach ($item in $processes) {
                if ($tunnelIds -contains [int]$item.ParentProcessId -and $tunnelIds -notcontains [int]$item.ProcessId) {
                    $tunnelIds += [int]$item.ProcessId; $added = $true
                }
            }
        } while ($added)
        $rows = @($connections | Where-Object { $tracked.Contains([int]$_.OwningProcess) -or $tunnelIds -contains [int]$_.OwningProcess } | ForEach-Object {
            [ordered]@{pid=[int]$_.OwningProcess; tunnel=($tunnelIds -contains [int]$_.OwningProcess); local_address=$_.LocalAddress; local_port=$_.LocalPort; remote_address=$_.RemoteAddress; remote_port=$_.RemotePort; state=[string]$_.State}
        })
        $record = [ordered]@{time=[DateTime]::UtcNow.ToString('o'); tracked_pids=@($tracked); connections=$rows}
    } catch {
        $record = [ordered]@{time=[DateTime]::UtcNow.ToString('o'); error=$_.Exception.GetType().Name}
    }
    $record | ConvertTo-Json -Depth 5 -Compress | Add-Content -LiteralPath $OutputFile -Encoding utf8
    if ($Once) { break }
    Start-Sleep -Milliseconds 500
}
