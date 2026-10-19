[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ArtifactDirectory,
    [Parameter(Mandatory)][string]$SourceSha
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = 'BorisDruzak/acti_potential'
$installers = @(Get-ChildItem -LiteralPath $ArtifactDirectory -Filter 'ACTISync-*-win-x64.msi' -File)
if ($installers.Count -ne 1) { throw 'Expected exactly one verified installer artifact.' }
$installer = $installers[0]
if ($installer.Name -notmatch '^ACTISync-(1\.(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]{0,4}))-win-x64\.msi$') {
    throw 'Invalid release coordinates.'
}
$version = $Matches[1]
$versionParts = $version.Split('.')
if ([int]$versionParts[1] -gt 255 -or [int]$versionParts[2] -gt 65535) { throw 'Invalid MSI version component.' }
if ($SourceSha -cnotmatch '^[a-f0-9]{40}$') { throw 'Full source commit required.' }
$tag = "acti-sync-v$version-$($SourceSha.Substring(0, 12))"
function Invoke-ReleaseGitHub([string[]]$Arguments) {
    $result = & gh @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'GitHub release operation failed.' }
    return $result
}
$releaseJson = & gh release view $tag --repo $repository --json isDraft,assets,targetCommitish 2>$null
$exists = $LASTEXITCODE -eq 0
$manifest = Join-Path $ArtifactDirectory 'acti-sync-release.json'
$existingManifest = ''
if ($exists) {
    $release = $releaseJson | ConvertFrom-Json
    $ref = (Invoke-ReleaseGitHub @('api', "repos/$repository/git/ref/tags/$tag")) | ConvertFrom-Json
    if ($ref.object.type -ne 'commit' -or $ref.object.sha -cne $SourceSha) { throw 'Existing release tag points to another commit.' }
    $downloadDirectory = Join-Path $ArtifactDirectory 'existing-release'
    New-Item -ItemType Directory -Force $downloadDirectory | Out-Null
    foreach ($name in @($installer.Name, 'acti-sync-release.json')) {
        if (@($release.assets | Where-Object name -eq $name).Count -gt 0) {
            Invoke-ReleaseGitHub @('release', 'download', $tag, '--repo', $repository, '--pattern', $name,
                '--dir', $downloadDirectory, '--clobber') | Out-Null
        } elseif (!$release.isDraft) { throw 'Published release is incomplete; refusing modification.' }
    }
    $previousInstaller = Join-Path $downloadDirectory $installer.Name
    if ((Test-Path -LiteralPath $previousInstaller) -and
        (Get-FileHash -LiteralPath $previousInstaller).Hash -cne (Get-FileHash -LiteralPath $installer.FullName).Hash) {
        throw 'Existing immutable installer differs from this build.'
    }
    $existingManifest = Join-Path $downloadDirectory 'acti-sync-release.json'
    if (!(Test-Path -LiteralPath $existingManifest)) { $existingManifest = '' }
}
& (Join-Path $PSScriptRoot 'sign-release.ps1') -Installer $installer.FullName -SourceSha $SourceSha `
    -OutputPath $manifest -ExistingManifest $existingManifest | Out-Null
if (!$exists) {
    Invoke-ReleaseGitHub @('release', 'create', $tag, '--repo', $repository, '--target', $SourceSha,
        '--title', "ACTI Sync $version", '--notes', "Verified ACTI Sync $version ($SourceSha).",
        '--draft') | Out-Null
    $release = [pscustomobject]@{isDraft=$true; assets=@()}
}
if ($release.isDraft) {
    foreach ($path in @($installer.FullName, $manifest)) {
        if (@($release.assets | Where-Object name -eq ([IO.Path]::GetFileName($path))).Count -eq 0) {
            Invoke-ReleaseGitHub @('release', 'upload', $tag, $path, '--repo', $repository) | Out-Null
        }
    }
    Invoke-ReleaseGitHub @('release', 'edit', $tag, '--repo', $repository, '--draft=false', '--latest') | Out-Null
}
Write-Output "Published verified ACTI Sync $version at tag $tag"
