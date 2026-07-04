# =========================================================================
#  agent.ps1 — Lightweight Windows Native Agent (Zero-Dependency)
#
#  Collects detailed CPU, RAM, disk, network, service metrics, and
#  file/directory monitoring data directly using CIM/WMI cmdlets,
#  then posts them as JSON to the Central Server.
# =========================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $ScriptDir "config.json"

# Default fallback configurations
$ServerUrl = "http://127.0.0.1:8000/api/report"
$Interval = 10
$HostnameOverride = ""
$DiskMounts = @("C:")
$Services = @()
$WatchedFiles = @()
$WatchedDirectories = @()
$LogFilePath = ""
$SyslogEnabled = $false

# Load config.json
if (Test-Path $ConfigFile) {
    try {
        $Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($Config.server_url) { $ServerUrl = $Config.server_url }
        if ($Config.interval) { $Interval = $Config.interval }
        if ($Config.hostname_override) { $HostnameOverride = $Config.hostname_override }
        if ($Config.disk_mount_points) { $DiskMounts = $Config.disk_mount_points }
        if ($Config.services) { $Services = $Config.services }
        if ($Config.watched_files) { $WatchedFiles = $Config.watched_files }
        if ($Config.watched_directories) { $WatchedDirectories = $Config.watched_directories }
        if ($Config.log_file) { $LogFilePath = $Config.log_file }
        if ($Config.PSObject.Properties.Name -contains 'syslog_enabled') { $SyslogEnabled = $Config.syslog_enabled }
    } catch {
        Write-Warning "Failed to parse config.json. Using defaults."
    }
}

# Determine Hostname
$HostName = if ($HostnameOverride) { $HostnameOverride } else { $env:COMPUTERNAME }

# -------------------------------------------------------------------------
# Structured Logging (stdout + file + Event Log)
# -------------------------------------------------------------------------
function Write-Log {
    param(
        [string]$Level = "INFO",
        [string]$Message
    )
    $Timestamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $LogLine = "$Timestamp [$Level] $Message"

    # 1. Always print to stdout
    Write-Host $LogLine

    # 2. Append to log file if configured
    if ($LogFilePath) {
        try {
            $LogDir = Split-Path -Parent $LogFilePath
            if ($LogDir -and -not (Test-Path $LogDir)) {
                New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
            }
            Add-Content -Path $LogFilePath -Value $LogLine -ErrorAction SilentlyContinue
        } catch {
            # Silently ignore file logging errors
        }
    }

    # 3. Write to Windows Event Log if syslog equivalent enabled
    if ($SyslogEnabled) {
        try {
            $EntryType = "Information"
            switch ($Level) {
                "ERROR"   { $EntryType = "Error" }
                "WARNING" { $EntryType = "Warning" }
            }
            # Create event source if not exists
            if (-not [System.Diagnostics.EventLog]::SourceExists("sysmon-agent")) {
                [System.Diagnostics.EventLog]::CreateEventSource("sysmon-agent", "Application")
            }
            Write-EventLog -LogName "Application" -Source "sysmon-agent" -EventId 1000 -EntryType $EntryType -Message $Message -ErrorAction SilentlyContinue
        } catch {
            # Event Log may require admin privileges — silently skip
        }
    }
}

# -------------------------------------------------------------------------
# Metric Collection Helpers
# -------------------------------------------------------------------------
function Get-CpuUsage {
    $CpuPerf = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
    $CpuInfo = Get-CimInstance Win32_ComputerSystem
    $Load = Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average

    return @{
        cpu_percent = [math]::Round($CpuPerf.PercentProcessorTime, 2)
        cpu_count_logical = $CpuInfo.NumberOfLogicalProcessors
        load_1m = [math]::Round($Load.Average, 2)  # Load estimation on Windows
        load_5m = [math]::Round($Load.Average, 2)
        load_15m = [math]::Round($Load.Average, 2)
    }
}

function Get-MemoryUsage {
    $OS = Get-CimInstance Win32_OperatingSystem
    $TotalKB = $OS.TotalVisibleMemorySize
    $FreeKB = $OS.FreePhysicalMemory
    
    $TotalBytes = $TotalKB * 1024
    $AvailableBytes = $FreeKB * 1024
    $UsedBytes = $TotalBytes - $AvailableBytes
    $UsedPercent = [math]::Round(($UsedBytes / $TotalBytes) * 100, 2)

    return @{
        total_bytes = $TotalBytes
        available_bytes = $AvailableBytes
        used_bytes = $UsedBytes
        used_percent = $UsedPercent
        buffers_bytes = 0  # Not directly applicable on Windows WMI
        cached_bytes = 0
        swap_total_bytes = $OS.SizeStoredInPagingFiles * 1024
        swap_used_bytes = ($OS.SizeStoredInPagingFiles - $OS.FreeSpaceInPagingFiles) * 1024
    }
}

function Get-DiskUsage {
    $DiskList = @{}
    foreach ($Mount in $DiskMounts) {
        # Format partition name like "C:"
        $DriveName = $Mount.TrimEnd("\")
        $Disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$DriveName'"
        if ($Disk) {
            $Total = $Disk.Size
            $Free = $Disk.FreeSpace
            $Used = $Total - $Free
            $Percent = [math]::Round(($Used / $Total) * 100, 2)

            $DiskList[$Mount] = @{
                total_bytes = $Total
                used_bytes = $Used
                free_bytes = $Free
                used_percent = $Percent
            }
        }
    }

    # Physical disk IO
    $DiskPerf = Get-CimInstance Win32_PerfRawData_PerfDisk_PhysicalDisk -Filter "Name='_Total'"
    $ReadBytes = $DiskPerf.DiskReadBytesPerSec
    $WriteBytes = $DiskPerf.DiskWriteBytesPerSec

    return @{
        disks = $DiskList
        io = @{
            device = "PhysicalDisk_Total"
            read_bytes = $ReadBytes
            write_bytes = $WriteBytes
        }
    }
}

function Get-NetworkUsage {
    $NetList = @{}
    $Adapters = Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface
    foreach ($Adapter in $Adapters) {
        # Skip local loopbacks
        if ($Adapter.Name -like "*Loopback*") { continue }
        
        $CleanName = $Adapter.Name -replace '[^a-zA-Z0-9]', '_'
        $NetList[$CleanName] = @{
            rx_bytes = $Adapter.BytesReceivedPerSec
            tx_bytes = $Adapter.BytesSentPerSec
        }
    }
    return $NetList
}

function Get-ServicesStatus {
    $SvcList = @{}
    foreach ($SvcName in $Services) {
        $Svc = Get-Service -Name $SvcName -ErrorAction SilentlyContinue
        if ($Svc) {
            $Status = "inactive"
            if ($Svc.Status -eq "Running") {
                $Status = "active"
            } elseif ($Svc.Status -eq "Stopped") {
                $Status = "inactive"
            }
            $SvcList[$SvcName] = $Status
        } else {
            $SvcList[$SvcName] = "not_found"
        }
    }
    return $SvcList
}

# -------------------------------------------------------------------------
# File & Directory Monitoring
# -------------------------------------------------------------------------
function Get-FileMonitoring {
    $Items = @()

    # 1. Monitor individual files
    foreach ($FilePath in $WatchedFiles) {
        if (Test-Path $FilePath -PathType Leaf) {
            $FileItem = Get-Item $FilePath -ErrorAction SilentlyContinue
            $FileHash = ""
            try {
                $FileHash = (Get-FileHash -Path $FilePath -Algorithm MD5 -ErrorAction SilentlyContinue).Hash.ToLower()
            } catch {
                $FileHash = "unknown"
            }

            $Items += @{
                path = $FilePath
                is_directory = $false
                exists = $true
                size_bytes = $FileItem.Length
                modified_time = [int][double]::Parse(($FileItem.LastWriteTimeUtc - [DateTime]::UnixEpoch).TotalSeconds.ToString())
                hash = $FileHash
                file_count = 0
            }
        } else {
            $Items += @{
                path = $FilePath
                is_directory = $false
                exists = $false
                size_bytes = 0
                modified_time = 0
                hash = ""
                file_count = 0
            }
        }
    }

    # 2. Monitor directories
    foreach ($DirPath in $WatchedDirectories) {
        if (Test-Path $DirPath -PathType Container) {
            $DirItem = Get-Item $DirPath -ErrorAction SilentlyContinue
            $ChildFiles = Get-ChildItem $DirPath -Recurse -File -ErrorAction SilentlyContinue
            $TotalSize = ($ChildFiles | Measure-Object -Property Length -Sum).Sum
            if (-not $TotalSize) { $TotalSize = 0 }
            $FileCount = ($ChildFiles | Measure-Object).Count

            $Items += @{
                path = $DirPath
                is_directory = $true
                exists = $true
                size_bytes = [long]$TotalSize
                modified_time = [int][double]::Parse(($DirItem.LastWriteTimeUtc - [DateTime]::UnixEpoch).TotalSeconds.ToString())
                hash = ""
                file_count = $FileCount
            }
        } else {
            $Items += @{
                path = $DirPath
                is_directory = $true
                exists = $false
                size_bytes = 0
                modified_time = 0
                hash = ""
                file_count = 0
            }
        }
    }

    return $Items
}

# -------------------------------------------------------------------------
# Main Send Report
# -------------------------------------------------------------------------
function Send-Report {
    $Timestamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    
    $Cpu = Get-CpuUsage
    $Mem = Get-MemoryUsage
    $Disk = Get-DiskUsage
    $Net = Get-NetworkUsage
    $Svc = Get-ServicesStatus
    $FileMon = Get-FileMonitoring

    $Payload = @{
        timestamp = $Timestamp
        os = "Windows"
        hostname = $HostName
        cpu = $Cpu
        memory = $Mem
        disk = $Disk.disks
        disk_io = $Disk.io
        network = $Net
        services = $Svc
        file_monitoring = $FileMon
    }

    $JsonPayload = ConvertTo-Json -InputObject $Payload -Depth 10

    Write-Log -Level "INFO" -Message "Sending metrics report to $ServerUrl..."
    try {
        $Response = Invoke-RestMethod -Uri $ServerUrl -Method Post -Body $JsonPayload -ContentType "application/json" -TimeoutSec 5
        Write-Log -Level "INFO" -Message "Metrics sent successfully"
    } catch {
        Write-Log -Level "ERROR" -Message "Failed to send metrics to Central Server: $_"
    }
}

# Main reporting loop
Write-Log -Level "INFO" -Message "sysmon-agent Native Windows starts monitoring node: $HostName"
Write-Log -Level "INFO" -Message "Server Target: $ServerUrl"
Write-Log -Level "INFO" -Message "Reporting every $Interval seconds. Watched files: $($WatchedFiles.Count), Watched dirs: $($WatchedDirectories.Count)"
if ($LogFilePath) {
    Write-Log -Level "INFO" -Message "Logging to file: $LogFilePath"
}

while ($true) {
    Send-Report
    Start-Sleep -Seconds $Interval
}
