[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ArtifactDirectory,
    [Parameter(Mandatory)][string]$SourceSha,
    [switch]$RequireAuthenticode,
    [bool]$ReleaseAsLatest = $true
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = 'BorisDruzak/acti_potential'
$installers = @(Get-ChildItem -LiteralPath $ArtifactDirectory -Filter 'ACTISync-*-win-x64.msi' -File)
if ($installers.Count -ne 1) { throw 'Expected exactly one verified installer artifact.' }
$installer = $installers[0]
. (Join-Path $PSScriptRoot 'AuthenticodeGate.ps1')
$signedFiles = @($installer.FullName) + @('Service','User','Updater','Tray' | ForEach-Object {
    Join-Path $ArtifactDirectory "$_/ACTISync.$_.exe"
})
$signatureStatuses = foreach ($file in $signedFiles) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Missing release component.' }
    (Get-AuthenticodeSignature -LiteralPath $file).Status.ToString()
}
$signatureMode = Get-PublicationSignatureMode -Statuses $signatureStatuses -Required:$RequireAuthenticode
if ($signatureMode -eq 'authenticode') {
    Assert-ReleaseAuthenticode -Files $signedFiles
} else { Write-Output 'Authenticode: not configured; publishing through the required RSA release-manifest trust path.' }
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
if ($exists) { $release = $releaseJson | ConvertFrom-Json }
# Server/document-only main commits may keep the installed agent version. Never
# publish another package at that version, or move GitHub latest backwards.
# Inspect the HTTP status so rate limits/network failures cannot look like a
# first publication. Only an actual 404 means that no prior release exists.
$latestResponse = & gh api "repos/$repository/releases/latest" --include 2>$null
$latestExit = $LASTEXITCODE
$latestText = $latestResponse -join "`n"
$responseParts = $latestText -split "`r?`n`r?`n", 2
if ($responseParts.Count -ne 2 -or $responseParts[0] -notmatch '\AHTTP/\S+ (\d{3})\b') {
    throw 'Cannot establish latest release HTTP status; refusing publication.'
}
$latestStatus = [int]$Matches[1]
$blocksPublication = $false
if ($latestStatus -eq 200 -and $latestExit -eq 0) {
    $latest = $responseParts[1] | ConvertFrom-Json
    if ($latest.draft -ne $false -or $latest.prerelease -ne $false -or
        $latest.tag_name -cnotmatch '^acti-sync-v(1\.(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]{0,4}))-[a-f0-9]{12}$') {
        throw 'Latest release is not a canonical ACTI Sync release.'
    }
    $latestVersion = [version]$Matches[1]
    if ($latestVersion.Minor -gt 255 -or $latestVersion.Build -gt 65535) { throw 'Invalid latest MSI version.' }
    $blocksPublication = $latest.tag_name -cne $tag -and $latestVersion -ge [version]$version
} elseif ($latestStatus -ne 404) {
    throw 'Latest release query failed; refusing publication.'
}
if ($blocksPublication -and (!$exists -or $release.isDraft)) {
    Write-Output "Skipped ACTI Sync $version publication: an equal or newer version is already published."
    return
}
$manifest = Join-Path $ArtifactDirectory 'acti-sync-release.json'
$existingManifest = ''
if ($exists) {
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
    $latestArgument = if ($ReleaseAsLatest) { '--latest' } else { '--latest=false' }
    Invoke-ReleaseGitHub @('release', 'create', $tag, '--repo', $repository, '--target', $SourceSha,
        '--title', "ACTI Sync $version", '--notes', "Verified ACTI Sync $version ($SourceSha).",
        '--draft', $latestArgument) | Out-Null
    $release = [pscustomobject]@{isDraft=$true; assets=@()}
}
if ($release.isDraft) {
    foreach ($path in @($installer.FullName, $manifest)) {
        if (@($release.assets | Where-Object name -eq ([IO.Path]::GetFileName($path))).Count -eq 0) {
            Invoke-ReleaseGitHub @('release', 'upload', $tag, $path, '--repo', $repository) | Out-Null
        }
    }
    $latestArgument = if ($ReleaseAsLatest) { '--latest' } else { '--latest=false' }
    Invoke-ReleaseGitHub @('release', 'edit', $tag, '--repo', $repository, '--draft=false', $latestArgument) | Out-Null
} elseif ($ReleaseAsLatest -and !$blocksPublication) {
    # Candidate publication can precede server rollout configuration. Promotion
    # retries must verify the exact tag and immutable signed assets above before
    # changing latest, and must never move latest behind an equal/newer release.
    Invoke-ReleaseGitHub @('release', 'edit', $tag, '--repo', $repository, '--latest') | Out-Null
}
Write-Output "Published verified ACTI Sync $version at tag $tag"
