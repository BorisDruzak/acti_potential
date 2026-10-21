[CmdletBinding()]
param([Parameter(Mandatory)][string]$ArtifactDirectory)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ACTI-PublicationPolicy-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $fixture | Out-Null
foreach ($name in @('publish-release.ps1','AuthenticodeGate.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $fixture
}
# The publication-argument tests never create/read a signing key or contact GitHub.
# RSA cryptography has its own tests; replace only that external publication step.
[IO.File]::WriteAllText((Join-Path $fixture 'sign-release.ps1'), @'
param($Installer,$SourceSha,$OutputPath,$ExistingManifest)
if ($ExistingManifest -and !(Test-Path -LiteralPath $ExistingManifest)) { throw 'Missing immutable manifest' }
return
'@)
$calls = [Collections.Generic.List[string]]::new()
$published = $false
$latestVersion = '1.3.8'
$installer = @(Get-ChildItem -LiteralPath $ArtifactDirectory -Filter 'ACTISync-*-win-x64.msi' -File)[0]
$candidateDirectory = Join-Path $fixture 'candidate'
New-Item -ItemType Directory $candidateDirectory | Out-Null
foreach ($component in @('Service','User','Updater','Tray')) {
    $destination = Join-Path $candidateDirectory $component
    New-Item -ItemType Directory $destination | Out-Null
    Copy-Item -LiteralPath (Join-Path $ArtifactDirectory "$component/ACTISync.$component.exe") -Destination $destination
}
Copy-Item -LiteralPath $installer.FullName -Destination $candidateDirectory
function gh {
    $calls.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'api') {
        if ($args[1] -like '*/git/ref/tags/*') { return '{"object":{"type":"commit","sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}' }
        return 'HTTP/2.0 200 synthetic' + "`ncontent-type: application/json`n`n" +
            ('{"draft":false,"prerelease":false,"tag_name":"acti-sync-v' + $latestVersion + '-aaaaaaaaaaaa"}')
    }
    if ($args[0] -eq 'release' -and $args[1] -eq 'view') {
        if (!$published) { $global:LASTEXITCODE=1; return }
        return @{isDraft=$false;assets=@(@{name=$installer.Name},@{name='acti-sync-release.json'})} | ConvertTo-Json -Depth 5 -Compress
    }
    if ($args[0] -eq 'release' -and $args[1] -eq 'download') {
        $name = $args[[Array]::IndexOf($args,'--pattern')+1]
        $destination = Join-Path $args[[Array]::IndexOf($args,'--dir')+1] $name
        if ($name -eq $installer.Name) { Copy-Item -LiteralPath $installer.FullName -Destination $destination }
        else { [IO.File]::WriteAllText($destination,'synthetic immutable manifest') }
    }
}
try {
    & (Join-Path $fixture 'publish-release.ps1') -ArtifactDirectory $candidateDirectory -SourceSha ('a'*40) -ReleaseAsLatest:$false
    $changes = @($calls | Where-Object { $_ -like 'release create *' -or $_ -like 'release edit *' })
    if ($changes.Count -ne 2 -or @($changes | Where-Object { $_ -notmatch '(?:^| )--latest=false(?: |$)' }).Count) {
        throw 'Candidate-only publication promoted latest'
    }
    $calls.Clear()
    & (Join-Path $fixture 'publish-release.ps1') -ArtifactDirectory $candidateDirectory -SourceSha ('a'*40)
    $changes = @($calls | Where-Object { $_ -like 'release create *' -or $_ -like 'release edit *' })
    if ($changes.Count -ne 2 -or @($changes | Where-Object { $_ -notmatch '(?:^| )--latest(?: |$)' }).Count) {
        throw 'Default publication no longer promotes latest'
    }
    Write-Output 'PASS candidate-only publication keeps latest=false for create/edit; default promotes canonical release'
    $calls.Clear(); $published=$true
    & (Join-Path $fixture 'publish-release.ps1') -ArtifactDirectory $candidateDirectory -SourceSha ('a'*40) -ReleaseAsLatest:$true
    $changes = @($calls | Where-Object { $_ -like 'release edit *' -and $_ -match '(?:^| )--latest(?: |$)' })
    if ($changes.Count -ne 1 -or @($calls | Where-Object { $_ -like 'release create *' -or $_ -like 'release upload *' }).Count) {
        throw 'Existing published candidate was not promoted without asset changes'
    }
    $calls.Clear(); $latestVersion='1.255.65535'
    & (Join-Path $fixture 'publish-release.ps1') -ArtifactDirectory $candidateDirectory -SourceSha ('a'*40) -ReleaseAsLatest:$true
    if (@($calls | Where-Object { $_ -like 'release edit *' -or $_ -like 'release create *' -or $_ -like 'release upload *' }).Count) {
        throw 'Existing older release replaced latest'
    }
    Write-Output 'PASS published candidate can promote after immutable verification; older release never replaces newer latest'
} finally {
    foreach ($name in @('publish-release.ps1','AuthenticodeGate.ps1','sign-release.ps1')) {
        Remove-Item -LiteralPath (Join-Path $fixture $name)
    }
    $existingDirectory = Join-Path $candidateDirectory 'existing-release'
    if (Test-Path -LiteralPath $existingDirectory) {
        foreach ($name in @($installer.Name,'acti-sync-release.json')) {
            $path = Join-Path $existingDirectory $name
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
        }
        Remove-Item -LiteralPath $existingDirectory
    }
    foreach ($component in @('Service','User','Updater','Tray')) {
        Remove-Item -LiteralPath (Join-Path $candidateDirectory "$component/ACTISync.$component.exe")
        Remove-Item -LiteralPath (Join-Path $candidateDirectory $component)
    }
    Remove-Item -LiteralPath (Join-Path $candidateDirectory $installer.Name)
    Remove-Item -LiteralPath $candidateDirectory
    Remove-Item -LiteralPath $fixture
}
