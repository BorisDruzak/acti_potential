[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$SourceSha,
    [string]$OutputPath = '',
    [string]$ExistingManifest = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$item = Get-Item -LiteralPath $Installer
if ($item.Name -notmatch '^ACTISync-(1\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4}))-win-x64\.msi$') {
    throw 'Installer name/version violates release contract.'
}
$version = $Matches[1]
$versionParts = $version.Split('.')
if ([int]$versionParts[1] -gt 255 -or [int]$versionParts[2] -gt 65535) { throw 'Invalid MSI version component.' }
if ($SourceSha -cnotmatch '^[0-9a-f]{40}$') { throw 'Full source commit required.' }
if ($item.Length -le 0 -or $item.Length -gt 512MB) { throw 'Installer exceeds release size limit.' }
$tag = "acti-sync-v$version-$($SourceSha.Substring(0, 12))"
if (!$OutputPath) { $OutputPath = Join-Path $item.DirectoryName 'acti-sync-release.json' }
$expected = [ordered]@{
    schema = 1
    product = 'ACTISync'
    version = $version
    source_sha = $SourceSha
    filename = $item.Name
    sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    size = $item.Length
    release_tag = $tag
    published_at = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
}
$publicKeyPath = Join-Path $PSScriptRoot '../../../backend/app/registry/acti_sync_release_public_key.pem'
$rsa = [Security.Cryptography.RSA]::Create()
$public = [Security.Cryptography.RSA]::Create()
try {
    $public.ImportFromPem([IO.File]::ReadAllText($publicKeyPath))
    if ($public.KeySize -ne 3072) { throw 'RSA3072 release trust key required.' }
    if ($ExistingManifest) {
        # Preserve the original timestamp and signed bytes on publication retries.
        $raw = [IO.File]::ReadAllText($ExistingManifest)
        if ([Text.Encoding]::UTF8.GetByteCount($raw) -gt 16384) { throw 'Existing manifest exceeds limit.' }
        $envelope = $raw | ConvertFrom-Json -AsHashtable
        if ($envelope.Count -ne 2 -or !$envelope.ContainsKey('payload') -or !$envelope.ContainsKey('signature')) {
            throw 'Invalid existing release envelope.'
        }
        $bytes = [Convert]::FromBase64String($envelope.payload)
        if (!$public.VerifyData($bytes, [Convert]::FromBase64String($envelope.signature),
                [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)) {
            throw 'Existing release signature invalid.'
        }
        $existing = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json -AsHashtable
        if ($existing.Count -ne $expected.Count) { throw 'Existing release payload fields differ.' }
        foreach ($field in $expected.Keys) {
            if ($field -eq 'published_at') { continue }
            if (!$existing.ContainsKey($field) -or $existing[$field] -cne $expected[$field]) {
                throw "Immutable existing release differs: $field"
            }
        }
        # ConvertFrom-Json may coerce ISO timestamps into DateTime depending on
        # PowerShell version; read this value as a JSON string explicitly.
        $payloadDocument = [Text.Json.JsonDocument]::Parse([Text.Encoding]::UTF8.GetString($bytes))
        try { $timestamp = [DateTimeOffset]::Parse($payloadDocument.RootElement.GetProperty('published_at').GetString()) }
        finally { $payloadDocument.Dispose() }
        if ($timestamp.Offset -ne [TimeSpan]::Zero -or $timestamp -gt [DateTimeOffset]::UtcNow.AddMinutes(10)) {
            throw 'Invalid existing publication time.'
        }
        [IO.File]::WriteAllText($OutputPath, $raw, [Text.UTF8Encoding]::new($false))
    } else {
        # The private key is provided only via Actions secret or a local process
        # environment. Never write it, include it in exceptions, or print it.
        if (!$env:ACTI_SYNC_RELEASE_SIGNING_KEY) { throw 'Release signing key is not configured.' }
        try { $rsa.ImportFromPem($env:ACTI_SYNC_RELEASE_SIGNING_KEY) }
        catch { throw 'Cannot load release signing key.' }
        if ($rsa.KeySize -ne 3072) { throw 'RSA3072 signing key required.' }
        $bytes = [Text.Encoding]::UTF8.GetBytes(($expected | ConvertTo-Json -Compress))
        $signature = $rsa.SignData($bytes, [Security.Cryptography.HashAlgorithmName]::SHA256,
            [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        if (!$public.VerifyData($bytes, $signature, [Security.Cryptography.HashAlgorithmName]::SHA256,
                [Security.Cryptography.RSASignaturePadding]::Pkcs1)) { throw 'Signing key does not match pinned trust key.' }
        $envelope = [ordered]@{payload=[Convert]::ToBase64String($bytes); signature=[Convert]::ToBase64String($signature)}
        [IO.File]::WriteAllText($OutputPath, ($envelope | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    }
    Write-Output $tag
} finally {
    $rsa.Dispose()
    $public.Dispose()
}
