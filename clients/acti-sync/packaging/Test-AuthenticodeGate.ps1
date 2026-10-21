$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AuthenticodeGate.ps1')
function Reject([scriptblock]$Action) { try { & $Action; throw 'TEST accepted invalid signer' } catch { if ($_.Exception.Message -eq 'TEST accepted invalid signer') { throw } } }
$valid = @{PrivateKey=$true; CodeSigning=$true; InDate=$true; Trusted=$true}
Assert-CodeSigningFacts @valid
foreach ($field in $valid.Keys) {
    $invalid = $valid.Clone(); $invalid[$field] = $false
    Reject { Assert-CodeSigningFacts @invalid }
}
Reject { Get-ReleaseSigningCertificate -Thumbprint '' -TimestampUrl '' -Required }
Reject { Get-ReleaseSigningCertificate -Thumbprint '../bad' -TimestampUrl 'https://timestamp.example' }
Reject { Assert-ReleaseSignatureFacts -Status 'NotSigned' -SignerMatches $true -Timestamped $true }
Reject { Assert-ReleaseSignatureFacts -Status 'Valid' -SignerMatches $false -Timestamped $true }
Reject { Assert-ReleaseSignatureFacts -Status 'Valid' -SignerMatches $true -Timestamped $false }
Assert-ReleaseSignatureFacts -Status 'Valid' -SignerMatches $true -Timestamped $true
if ($null -ne (Get-ReleaseSigningCertificate -Thumbprint '' -TimestampUrl '')) { throw 'Optional RSA-only path rejected' }
Write-Output 'PASS Authenticode private-key/EKU/expiry/trust/timestamp/signer gates; optional RSA path'

foreach ($mixed in @('HashMismatch','NotTrusted','Valid')) {
    Reject { Get-PublicationSignatureMode -Statuses @('NotSigned','NotSigned',$mixed,'NotSigned','NotSigned') }
}
if ((Get-PublicationSignatureMode -Statuses @('NotSigned','NotSigned','NotSigned','NotSigned','NotSigned')) -ne 'rsa') {
    throw 'All-unsigned publication rejected'
}
Reject { Get-PublicationSignatureMode -Statuses @('NotSigned','NotSigned','NotSigned','NotSigned','NotSigned') -Required }
Write-Output 'PASS RSA-only publication requires all five components unsigned; mixed/invalid/required unsigned signatures rejected'
