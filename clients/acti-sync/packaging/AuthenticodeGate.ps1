# Authenticode publisher trust is separate from the schema-1 RSA release signature.
function Get-PublicationSignatureMode([string[]]$Statuses, [switch]$Required) {
    if ($Statuses.Count -ne 5) { throw 'Publication requires four EXEs and one MSI.' }
    if (!$Required -and @($Statuses | Where-Object { $_ -ne 'NotSigned' }).Count -eq 0) { return 'rsa' }
    if (@($Statuses | Where-Object { $_ -ne 'Valid' }).Count -gt 0) {
        throw 'Mixed, unsigned or invalid Authenticode release components; refusing publication.'
    }
    return 'authenticode'
}
function Assert-CodeSigningFacts([bool]$PrivateKey, [bool]$CodeSigning, [bool]$InDate, [bool]$Trusted) {
    if (!$PrivateKey -or !$CodeSigning -or !$InDate -or !$Trusted) {
        throw 'A current, trusted Code Signing certificate with its private key is required.'
    }
}
function Assert-ReleaseSignatureFacts([string]$Status, [bool]$SignerMatches, [bool]$Timestamped) {
    if ($Status -ne 'Valid' -or !$SignerMatches -or !$Timestamped) {
        throw 'Release requires valid, consistently signed and timestamped EXEs and MSI.'
    }
}
function Test-CodeSigningEku($Certificate) {
    $eku = @($Certificate.Extensions | Where-Object { $_ -is [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] })
    return @($eku | ForEach-Object { $_.EnhancedKeyUsages } | Where-Object { $_.Value -eq '1.3.6.1.5.5.7.3.3' }).Count -gt 0
}
function Test-PublisherChain($Certificate) {
    $chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $chain.ChainPolicy.RevocationMode = 'Online'
        $chain.ChainPolicy.RevocationFlag = 'EntireChain'
        $chain.ChainPolicy.VerificationFlags = 'NoFlag'
        $chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromSeconds(15)
        [void]$chain.ChainPolicy.ApplicationPolicy.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.3'))
        return $chain.Build($Certificate)
    } finally { $chain.Dispose() }
}
function Get-ReleaseSigningCertificate([string]$Thumbprint, [string]$TimestampUrl, [switch]$Required) {
    if (!$Thumbprint) {
        if ($Required) { throw 'Required Authenticode signing has no configured Code Signing certificate.' }
        return $null
    }
    if ($Thumbprint -cnotmatch '^[a-fA-F0-9]{40}$') { throw 'Invalid Code Signing certificate thumbprint.' }
    $timestamp = $null
    if (![Uri]::TryCreate($TimestampUrl, [UriKind]::Absolute, [ref]$timestamp) -or
        $timestamp.Scheme -notin @('http','https') -or $timestamp.UserInfo -or $timestamp.Fragment) {
        throw 'A valid timestamp authority URL is required for Authenticode signing.'
    }
    $certificate = Get-Item -LiteralPath "Cert:/CurrentUser/My/$Thumbprint"
    $now = [DateTime]::UtcNow
    Assert-CodeSigningFacts -PrivateKey $certificate.HasPrivateKey -CodeSigning (Test-CodeSigningEku $certificate) `
        -InDate ($now -ge $certificate.NotBefore.ToUniversalTime() -and $now -lt $certificate.NotAfter.ToUniversalTime()) `
        -Trusted (Test-PublisherChain $certificate)
    return $certificate
}
function Assert-ReleaseAuthenticode([string[]]$Files, [string]$Thumbprint = '') {
    if ($Files.Count -ne 5) { throw 'Authenticode gate requires exactly four EXEs and the MSI.' }
    foreach ($file in $Files) {
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Missing signed release component.' }
        $signature = Get-AuthenticodeSignature -LiteralPath $file
        $signer = $signature.SignerCertificate
        $timestamp = $signature.TimeStamperCertificate
        if ($null -eq $signer) { throw 'Missing release signer.' }
        if (!$Thumbprint) { $Thumbprint = $signer.Thumbprint }
        Assert-ReleaseSignatureFacts -Status $signature.Status.ToString() `
            -SignerMatches ($signer.Thumbprint -eq $Thumbprint -and (Test-CodeSigningEku $signer)) `
            -Timestamped ($null -ne $timestamp)
        if (!(Test-PublisherChain $signer)) { throw 'Release publisher chain is not trusted.' }
        # WinVerifyTrust (Status Valid) verifies the timestamp signature and its chain.
    }
}
