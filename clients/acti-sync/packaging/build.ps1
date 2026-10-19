[CmdletBinding()]
param(
    [string]$Version = '1.2.0',
    [string]$OutputDirectory = '',
    [string]$DotNet = 'dotnet',
    [string]$Wix = '',
    [string]$SignCertificateThumbprint = '',
    [string]$TimestampUrl = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$clientRoot = Split-Path $PSScriptRoot -Parent
if (!$OutputDirectory) { $OutputDirectory = Join-Path $clientRoot 'artifacts' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
function Run-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Build step failed: $Program (exit $LASTEXITCODE)" }
}
Push-Location $clientRoot
try {
    if ((& $DotNet --version).Trim() -ne '10.0.402') { throw 'Install the pinned .NET SDK 10.0.402.' }
    if (!$Wix) {
        $toolDirectory = Join-Path $OutputDirectory 'tools'
        $Wix = Join-Path $toolDirectory 'wix.exe'
        if (!(Test-Path -LiteralPath $Wix)) {
            Run-Native $DotNet @('tool', 'install', 'wix', '--version', '6.0.2', '--tool-path', $toolDirectory)
        }
    }
    if (!((& $Wix --version).Trim().StartsWith('6.0.2'))) { throw 'WiX 6.0.2 required.' }
    Run-Native $Wix @('extension', 'add', '-g', 'WixToolset.Util.wixext/6.0.2')
    Run-Native $DotNet @('run', '--project', 'ACTISync.Tests', '-c', 'Release')
    foreach ($component in @('Service', 'User')) {
        $destination = Join-Path $OutputDirectory $component
        Run-Native $DotNet @('publish', "ACTISync.$component/ACTISync.$component.csproj", '-c', 'Release',
            '-r', 'win-x64', '--self-contained', 'true', '-p:PublishSingleFile=true',
            '-p:IncludeNativeLibrariesForSelfExtract=true', '-p:DebugType=None', '-p:DebugSymbols=false',
            "-p:Version=$Version", '-o', $destination)
        $publishedFiles = @(Get-ChildItem -LiteralPath $destination -File)
        if ($publishedFiles.Count -ne 1 -or $publishedFiles[0].Name -ne "ACTISync.$component.exe") {
            throw "Expected one self-contained $component EXE; refusing incomplete MSI payload."
        }
    }
    if ($SignCertificateThumbprint) {
        $certificate = Get-Item -LiteralPath "Cert:/CurrentUser/My/$SignCertificateThumbprint"
        foreach ($executable in @(Join-Path $OutputDirectory 'Service/ACTISync.Service.exe'; Join-Path $OutputDirectory 'User/ACTISync.User.exe')) {
            $signParameters = @{FilePath=$executable; Certificate=$certificate; HashAlgorithm='SHA256'}
            if ($TimestampUrl) { $signParameters.TimestampServer = $TimestampUrl }
            $result = Set-AuthenticodeSignature @signParameters
            if ($result.Status -ne 'Valid') { throw 'EXE Authenticode signing failed.' }
        }
    }
    $msi = Join-Path $OutputDirectory "ACTISync-$Version-win-x64.msi"
    Run-Native $Wix @('build', (Join-Path $PSScriptRoot 'ACTISync.wxs'), '-arch', 'x64',
        '-ext', 'WixToolset.Util.wixext/6.0.2', '-d', "Version=$Version",
        '-d', "ServiceDir=$(Join-Path $OutputDirectory 'Service')",
        '-d', "UserDir=$(Join-Path $OutputDirectory 'User')", '-o', $msi)
    if ($SignCertificateThumbprint) {
        $signParameters.FilePath = $msi
        if ((Set-AuthenticodeSignature @signParameters).Status -ne 'Valid') { throw 'MSI signing failed.' }
    }
    $files = @($msi, (Join-Path $OutputDirectory 'Service/ACTISync.Service.exe'), (Join-Path $OutputDirectory 'User/ACTISync.User.exe'))
    $manifest = foreach ($file in $files) {
        $item = Get-Item -LiteralPath $file
        [ordered]@{name=$item.Name; bytes=$item.Length; sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant(); signature=(Get-AuthenticodeSignature -LiteralPath $file).Status.ToString()}
    }
    $manifest | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $OutputDirectory 'manifest.json')
    Write-Output "Single installer (includes service and user component): $msi"
} finally { Pop-Location }
