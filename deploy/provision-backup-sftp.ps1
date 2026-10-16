param(
    [Parameter(Mandatory=$true)][string]$RootPath,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z][a-z0-9_]{2,24}$')][string]$UserName,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z][a-z0-9_]{2,24}$')][string]$GroupName,
    [Parameter(Mandatory=$true)][string]$PublicKeyBase64,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$ExpectedConfigSha256
)
# Run through an existing verified administrator SSH session. Only the new
# directory/account/group and their explicit sshd scope are changed.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$utf8=[Text.UTF8Encoding]::new($false)
$root=[IO.Path]::GetFullPath($RootPath).TrimEnd('\')
if ($root -notmatch '^[A-Z]:\\[^\\]+$' -or $root.Substring(0,3) -ne 'D:\') {throw 'Dedicated top-level D directory required'}
if (Test-Path -LiteralPath $root) {throw 'Destination already exists; refuse to modify it'}
if (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue) {throw 'Account already exists'}
if (Get-LocalGroup -Name $GroupName -ErrorAction SilentlyContinue) {throw 'Group already exists'}
if ((Get-Item -LiteralPath 'D:\').Attributes -band [IO.FileAttributes]::ReparsePoint) {throw 'Reparse parent refused'}
$config=Join-Path $env:ProgramData 'ssh\sshd_config'
$sshd=Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'
if ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ne $ExpectedConfigSha256) {throw 'SSHD configuration changed since inspection'}
$publicKey=$utf8.GetString([Convert]::FromBase64String($PublicKeyBase64)).Trim()
if ($publicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?$') {throw 'Unexpected public key format'}
$original=[IO.File]::ReadAllText($config)
if ($original -match '(?im)^\s*Deny(?:Users|Groups)\s+') {throw 'Explicit deny policy requires source review'}
if ($original -match '(?im)^\s*AllowUsers\s+') {throw 'Explicit user allow policy requires source review'}
$ownerId=[guid]::NewGuid().ToString()
$admin=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$system=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
function Protect-Directory([string]$Path,[Security.Principal.SecurityIdentifier]$BackupSid,[string]$Rights) {
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false);$acl.SetOwner($admin)
    foreach ($sid in @($admin,$system)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
    }
    if ($BackupSid) {$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($BackupSid,$Rights,'ContainerInherit,ObjectInherit','None','Allow'))}
    Set-Acl -LiteralPath $Path -AclObject $acl
}
[void][IO.Directory]::CreateDirectory($root)
Protect-Directory $root $null ''
$ops=Join-Path $root 'ops';[void][IO.Directory]::CreateDirectory($ops);Protect-Directory $ops $null ''
[IO.File]::Copy($config,(Join-Path $ops 'sshd_config.before'),$false)
[IO.File]::WriteAllText((Join-Path $ops 'sshd_acl.before.txt'),(Get-Acl -LiteralPath $config).Sddl,$utf8)
[IO.File]::WriteAllText((Join-Path $root '.acti-backup-owner'),$ownerId,$utf8)
$random=[byte[]]::new(64);$rng=[Security.Cryptography.RandomNumberGenerator]::Create()
try {$rng.GetBytes($random)} finally {$rng.Dispose()}
$password=ConvertTo-SecureString ('Aa1!'+[Convert]::ToBase64String($random)) -AsPlainText -Force
$account=New-LocalUser -Name $UserName -Password $password -PasswordNeverExpires -UserMayNotChangePassword -Description 'ACTI encrypted backup; key-only SFTP'
$password.Dispose();[Array]::Clear($random,0,$random.Length)
$group=New-LocalGroup -Name $GroupName -Description 'ACTI dedicated SFTP backup access'
Add-LocalGroupMember -Group $group -Member $account
Protect-Directory $root $account.SID 'ReadAndExecute'
Protect-Directory $ops $null ''
$repository=Join-Path $root 'repository';[void][IO.Directory]::CreateDirectory($repository)
Protect-Directory $repository $account.SID 'Modify'
$keys=Join-Path $root 'keys';[void][IO.Directory]::CreateDirectory($keys)
Protect-Directory $keys $account.SID 'ReadAndExecute'
$keyPath=Join-Path $keys 'authorized_keys';[IO.File]::WriteAllText($keyPath,$publicKey+"`n",$utf8)
$candidate=$original
# Extend an existing whitelist with only the new, otherwise empty dedicated group.
$candidate=[regex]::Replace($candidate,'(?im)^(\s*AllowGroups\s+[^\r\n]+)',{param($m) $m.Value+' '+$GroupName})
$chroot=$root.Replace('\','/');$keyUnix=$keyPath.Replace('\','/')
$scope=@"
# BEGIN ACTI dedicated backup account
Match User $UserName
    ChrootDirectory $chroot
    ForceCommand internal-sftp -d /repository
    AuthorizedKeysFile $keyUnix
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
# END ACTI dedicated backup account

"@
$firstMatch=[regex]::Match($candidate,'(?im)^Match\s+')
if ($firstMatch.Success) {$candidate=$candidate.Insert($firstMatch.Index,$scope+"`r`n")}
else {$candidate=$candidate.TrimEnd()+"`r`n"+$scope}
$candidatePath=Join-Path $ops 'sshd_config.candidate';[IO.File]::WriteAllText($candidatePath,$candidate,$utf8)
& $sshd -t -f $candidatePath 2>$null
if ($LASTEXITCODE) {throw 'Candidate SSH configuration invalid; live configuration unchanged'}
$before=@(& $sshd -T -f $config -C "user=$env:USERNAME,host=localhost,addr=127.0.0.1" 2>$null)
if ($LASTEXITCODE) {throw 'Cannot inspect administrator effective configuration'}
$after=@(& $sshd -T -f $candidatePath -C "user=$env:USERNAME,host=localhost,addr=127.0.0.1" 2>$null)
if ($LASTEXITCODE) {throw 'Cannot inspect candidate effective configuration'}
if (Compare-Object ($before | Where-Object {$_ -notmatch '^allowgroups '}) ($after | Where-Object {$_ -notmatch '^allowgroups '})) {throw 'Existing administrator settings changed'}
# Windows sshd -T may perform blocking domain lookups for a newly created local
# identity. Validate syntax and the unchanged administrator here; actual key-only
# SFTP, chroot and shell-denial probes must confirm the new identity after apply.
if ($candidate -notmatch ('(?m)^Match User '+[regex]::Escape($UserName)+'\r?$') -or $candidate -notmatch '(?m)^    ForceCommand internal-sftp -d /repository\r?$') {throw 'Dedicated account scope absent'}
$state=[ordered]@{owner_id=$ownerId;root=$root;user=$UserName;group=$GroupName;account_sid=$account.SID.Value;config_original_sha256=$ExpectedConfigSha256;candidate_sha256=(Get-FileHash -LiteralPath $candidatePath -Algorithm SHA256).Hash;status='prepared';existing_admin_settings_preserved=$true}
[IO.File]::WriteAllText((Join-Path $ops 'provision.json'),($state | ConvertTo-Json -Depth 4),$utf8)
# Apply asynchronously so that restarting sshd does not strand the coordinator.
# The original bytes are restored unless a fresh session confirms readiness.
$apply=@'
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$live=Join-Path $env:ProgramData 'ssh\sshd_config'
$result='not_applied'
try {
    [IO.File]::WriteAllBytes($live,[IO.File]::ReadAllBytes((Join-Path $root 'sshd_config.candidate')))
    Restart-Service sshd -ErrorAction Stop
    $result='applied_waiting_for_confirmation'
    Start-Sleep -Seconds 120
    if (-not (Test-Path -LiteralPath (Join-Path $root 'access-confirmed'))) {
        [IO.File]::WriteAllBytes($live,[IO.File]::ReadAllBytes((Join-Path $root 'sshd_config.before')))
        Restart-Service sshd -ErrorAction Stop
        $result='rolled_back_no_confirmation'
    } else {$result='confirmed'}
} catch {
    [IO.File]::WriteAllBytes($live,[IO.File]::ReadAllBytes((Join-Path $root 'sshd_config.before')))
    Restart-Service sshd -ErrorAction SilentlyContinue
    $result='rolled_back_error'
}
[IO.File]::WriteAllText((Join-Path $root 'ssh-apply-result.txt'),$result,[Text.UTF8Encoding]::new($false))
'@
$applyPath=Join-Path $ops 'apply-ssh.ps1';[IO.File]::WriteAllText($applyPath,$apply,$utf8)
$taskName='ACTI-Backup-SSH-Apply'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {throw 'Apply task name already exists'}
$action=New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$applyPath+'"')
$trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(8)
$taskSettings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $taskSettings -User 'SYSTEM' -RunLevel Highest -Description 'Apply scoped ACTI SFTP settings with automatic rollback' | Out-Null
$state | ConvertTo-Json -Depth 4 -Compress
