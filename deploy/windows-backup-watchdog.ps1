#requires -Version 5.1
<#
Checks restic repository metadata without a password or reading backup contents.
Provision .acti-backup-owner and an ops directory owned by SYSTEM/Administrators
before scheduling. Register the ACTIBackup Application event source separately.
Snapshot file time is a freshness signal, not proof of decryptability or restore.
CheckOnly is read-only: JSON and exit status, without reports or Event Log writes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RootPath,
    [Parameter(Mandatory = $true)][guid]$ExpectedOwnerId,
    [ValidateRange(1, 8760)][double]$MaximumAgeHours = 30,
    [ValidateRange(0, 1048576)][double]$MinimumFreeGiB = 100,
    [ValidateRange(0.000000001, 1048576)][double]$MaximumRepositoryGiB = 300,
    [ValidateRange(1, 1000000)][int]$MaximumEntries = 1000000,
    [ValidateRange(1, 60)][int]$MaximumScanSeconds = 60,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$checkedUtc = [DateTime]::UtcNow
$issues = New-Object 'System.Collections.Generic.List[string]'
$report = [ordered]@{
    format = 'acti-backup-watchdog-v1'
    checked_utc = $checkedUtc.ToString('o')
    check_only = [bool]$CheckOnly
    status = 'unhealthy'
    event_id = 1004
    snapshot_count = 0
    newest_snapshot_utc = $null
    snapshot_age_hours = $null
    repository_bytes = $null
    repository_entries = 0
    free_gib = $null
    maximum_age_hours = $MaximumAgeHours
    minimum_free_gib = $MinimumFreeGiB
    maximum_repository_gib = $MaximumRepositoryGiB
    issues = @()
}
$ownedRoot = $false
$ops = $null

function Assert-PlainItem([string]$LiteralPath, [bool]$Directory) {
    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'reparse_point_refused'
    }
    if ($item.PSIsContainer -ne $Directory) { throw 'unexpected_path_type' }
    return $item
}

function Assert-PlainAncestors([string]$LiteralPath) {
    $cursor = $LiteralPath
    while ($cursor) {
        $null = Assert-PlainItem $cursor $true
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName
    }
}

function Assert-PrivateOps([string]$LiteralPath) {
    $null = Assert-PlainItem $LiteralPath $true
    $acl = Get-Acl -LiteralPath $LiteralPath
    $allowed = @('S-1-5-18', 'S-1-5-32-544')
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $allowed) { throw 'ops_permissions_unsafe' }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $rule.IdentityReference.Value -notin $allowed) {
            throw 'ops_permissions_unsafe'
        }
    }
}

try {
    if ($RootPath -notmatch '^[A-Za-z]:[\\/]') { throw 'local_absolute_root_required' }
    $root = [IO.Path]::GetFullPath($RootPath).TrimEnd([char[]]'\/')
    if ($root.Length -le 3 -or $root -ne $RootPath.TrimEnd([char[]]'\/')) {
        throw 'noncanonical_root_refused'
    }
    Assert-PlainAncestors $root
    $marker = Assert-PlainItem (Join-Path $root '.acti-backup-owner') $false
    if ($marker.Length -gt 128 -or $marker.Length -lt 36) { throw 'owner_mismatch' }
    # The ownership marker is the only file whose contents are read.
    $ownerText = [IO.File]::ReadAllText($marker.FullName).Trim()
    if ($ownerText -ne $ExpectedOwnerId.ToString('D')) { throw 'owner_mismatch' }
    $ownedRoot = $true
    $ops = Join-Path $root 'ops'
    $null = Assert-PlainItem $ops $true
    if (-not $CheckOnly) { Assert-PrivateOps $ops }
    $repository = Join-Path $root 'repository'
    $repositoryInfo = Assert-PlainItem $repository $true
    $configPath = Join-Path $repository 'config'
    if (-not (Test-Path -LiteralPath $configPath)) { throw 'repository_config_missing' }
    $config = Assert-PlainItem $configPath $false
    if ($config.Length -eq 0) { throw 'repository_config_missing' }
    $snapshots = Join-Path $repository 'snapshots'
    $null = Assert-PlainItem $snapshots $true
    $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($root))
    $report.free_gib = [Math]::Round($drive.AvailableFreeSpace / 1GB, 3)
    if ($drive.AvailableFreeSpace -lt $MinimumFreeGiB * 1GB) {
        $issues.Add('free_space_limit')
    }

    $queue = New-Object 'System.Collections.Generic.Queue[System.IO.DirectoryInfo]'
    $queue.Enqueue($repositoryInfo)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    [long]$bytes = 0
    $newest = $null
    $prefix = $repository.TrimEnd('\') + '\'
    while ($queue.Count -gt 0) {
        $directory = $queue.Dequeue()
        $null = Assert-PlainItem $directory.FullName $true
        # Stream one directory at a time; never use a recursive filesystem walk.
        foreach ($entry in $directory.EnumerateFileSystemInfos()) {
            if ($timer.Elapsed.TotalSeconds -gt $MaximumScanSeconds -or
                $report.repository_entries -ge $MaximumEntries) {
                throw 'repository_scan_limit'
            }
            $report.repository_entries++
            $entry.Refresh()
            if (-not $entry.FullName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'repository_escape_refused'
            }
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'reparse_point_refused'
            }
            if (($entry.Attributes -band [IO.FileAttributes]::Directory) -ne 0) {
                $queue.Enqueue([IO.DirectoryInfo]$entry)
            } else {
                $bytes += $entry.Length
                if ($entry.DirectoryName -eq $snapshots -and $entry.Name -match '^[0-9a-f]{64}$') {
                    $report.snapshot_count++
                    if ($null -eq $newest -or $entry.LastWriteTimeUtc -gt $newest) {
                        $newest = $entry.LastWriteTimeUtc
                    }
                }
            }
        }
    }
    $timer.Stop()
    $report.repository_bytes = $bytes
    if ($bytes -gt $MaximumRepositoryGiB * 1GB) { $issues.Add('repository_size_limit') }
    if ($null -eq $newest) {
        $issues.Add('snapshot_missing')
    } else {
        $report.newest_snapshot_utc = $newest.ToString('o')
        $age = ($checkedUtc - $newest).TotalHours
        $report.snapshot_age_hours = [Math]::Round($age, 3)
        if ($age -lt -(5.0 / 60)) { $issues.Add('snapshot_timestamp_invalid') }
        elseif ($age -gt $MaximumAgeHours) { $issues.Add('snapshot_stale') }
    }
    if ($issues.Count -eq 0) {
        $report.status = 'healthy'
        $report.event_id = 1000
    } elseif ('free_space_limit' -in $issues -or 'repository_size_limit' -in $issues) {
        $report.event_id = 1003
    } else {
        $report.event_id = 1002
    }
} catch {
    $known = @(
        'local_absolute_root_required', 'noncanonical_root_refused', 'owner_mismatch',
        'reparse_point_refused', 'unexpected_path_type', 'repository_config_missing',
        'repository_escape_refused', 'repository_scan_limit', 'ops_permissions_unsafe'
    )
    $reason = $_.Exception.Message
    if ($reason -notin $known) { $reason = 'metadata_check_failed' }
    $issues.Add($reason)
    $report.repository_bytes = $null
    if ($reason -eq 'repository_scan_limit') { $report.event_id = 1003 }
    else { $report.event_id = 1004 }
}

if (-not $CheckOnly) {
    try {
        if ($ownedRoot -and $null -ne $ops) {
            Assert-PlainAncestors $ops
            Assert-PrivateOps $ops
            $report.issues = @($issues.ToArray())
            $json = $report | ConvertTo-Json -Depth 4 -Compress
            $name = 'watchdog-' + $checkedUtc.ToString('yyyyMMddTHHmmssZ') + '-' +
                [guid]::NewGuid().ToString('N') + '.json'
            # CreateNew cannot overwrite an existing file or hard link.
            $stream = New-Object IO.FileStream (
                (Join-Path $ops $name), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            try {
                $encoded = (New-Object Text.UTF8Encoding $false).GetBytes($json + "`n")
                $stream.Write($encoded, 0, $encoded.Length)
                $stream.Flush($true)
            } finally { $stream.Dispose() }
        }
    } catch {
        $issues.Add('report_write_failed')
        $report.status = 'unhealthy'
        $report.event_id = 1004
    }
    try {
        $report.issues = @($issues.ToArray())
        $json = $report | ConvertTo-Json -Depth 4 -Compress
        $type = [Diagnostics.EventLogEntryType]::Information
        if ($report.status -ne 'healthy') { $type = [Diagnostics.EventLogEntryType]::Error }
        if (-not [Diagnostics.EventLog]::SourceExists('ACTIBackup')) {
            throw 'event_source_missing'
        }
        [Diagnostics.EventLog]::WriteEntry('ACTIBackup', $json, $type, $report.event_id)
    } catch {
        $issues.Add('event_log_write_failed')
        $report.status = 'unhealthy'
        $report.event_id = 1004
    }
}
$report.issues = @($issues.ToArray())
$report | ConvertTo-Json -Depth 4 -Compress
if ($report.status -eq 'healthy') { exit 0 }
exit 1
