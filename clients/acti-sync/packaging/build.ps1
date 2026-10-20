[CmdletBinding()]
param(
    [string]$Version = '1.3.7',
    [string]$SourceSha = '',
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
    if (!$SourceSha) { $SourceSha = (& git rev-parse HEAD).Trim() }
    if ($SourceSha -cnotmatch '^[0-9a-f]{40}$') { throw 'SourceSha must be a canonical Git SHA.' }
    if ($Version -notmatch '^1\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})$' -or ([version]$Version).Minor -gt 255 -or ([version]$Version).Build -gt 65535) { throw 'Invalid ACTI Sync release version.' }
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
    Run-Native $DotNet @('run', '--project', 'ACTISync.Updater.Tests', '-c', 'Release')
    Run-Native $DotNet @('run', '--project', 'ACTISync.InstallerActions.Tests', '-c', 'Release')
    foreach ($component in @('Service', 'User', 'Updater', 'Tray')) {
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
        foreach ($executable in @(Join-Path $OutputDirectory 'Service/ACTISync.Service.exe'; Join-Path $OutputDirectory 'User/ACTISync.User.exe'; Join-Path $OutputDirectory 'Updater/ACTISync.Updater.exe'; Join-Path $OutputDirectory 'Tray/ACTISync.Tray.exe')) {
            $signParameters = @{FilePath=$executable; Certificate=$certificate; HashAlgorithm='SHA256'}
            if ($TimestampUrl) { $signParameters.TimestampServer = $TimestampUrl }
            $result = Set-AuthenticodeSignature @signParameters
            if ($result.Status -ne 'Valid') { throw 'EXE Authenticode signing failed.' }
        }
    }
    $installerActionsDirectory = Join-Path $OutputDirectory 'InstallerActions'
    Run-Native $DotNet @('build', 'ACTISync.InstallerActions/ACTISync.InstallerActions.csproj', '-c', 'Release',
        '-p:Platform=x64', "-p:InstallerLifecycleExePath=$(Join-Path $OutputDirectory 'Updater/ACTISync.Updater.exe')", '-o', $installerActionsDirectory)
    if (!(Test-Path -LiteralPath (Join-Path $installerActionsDirectory 'ACTISync.InstallerActions.CA.dll'))) {
        throw 'Expected an embedded DTF custom-action DLL; refusing an MSI without server validation.'
    }
    $coldClrCompiler = Join-Path $env:SystemRoot 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if (!(Test-Path -LiteralPath $coldClrCompiler)) { throw 'Windows .NET Framework x64 compiler required for cold CLR regression test.' }
    $coldClrFixture = Join-Path $clientRoot 'ACTISync.InstallerActions.Tests/ColdClrHost.cs'
    $coldClrExe = Join-Path $installerActionsDirectory 'ACTISync.InstallerActions.ColdClrHost.exe'
    Run-Native $coldClrCompiler @('/nologo', '/target:exe', '/platform:x64', "/out:$coldClrExe", $coldClrFixture)
    Run-Native $coldClrExe @((Join-Path $installerActionsDirectory 'ACTISync.InstallerActions.dll'),
        (Join-Path $OutputDirectory 'Updater/ACTISync.Updater.exe'))
    $msi = Join-Path $OutputDirectory "ACTISync-$Version-win-x64.msi"
    Run-Native $Wix @('build', (Join-Path $PSScriptRoot 'ACTISync.wxs'), (Join-Path $PSScriptRoot 'ServerUI.wxs'), '-arch', 'x64',
        '-ext', 'WixToolset.Util.wixext/6.0.2', '-d', "Version=$Version",
        '-d', "SourceSha=$SourceSha",
        '-d', "InstallerActionsDir=$installerActionsDirectory",
        '-d', "ServiceDir=$(Join-Path $OutputDirectory 'Service')",
        '-d', "UserDir=$(Join-Path $OutputDirectory 'User')",
        '-d', "UpdaterDir=$(Join-Path $OutputDirectory 'Updater')", '-d', "TrayDir=$(Join-Path $OutputDirectory 'Tray')", '-o', $msi)
    # ICE20 assumes a legacy FilesInUse/Ignore dialog. This MSI deliberately uses
    # authenticated drain before replacement; validate the remaining ICE20 UI
    # requirements and the lifecycle ordering directly against the actual MSI.
    $contractExe = Join-Path $installerActionsDirectory 'ACTISync.MsiContractChecks.exe'
    $dtf = Join-Path $installerActionsDirectory 'WixToolset.Dtf.WindowsInstaller.dll'
    Run-Native $coldClrCompiler @('/nologo', '/target:exe', '/platform:x64', "/out:$contractExe", "/reference:$dtf",
        (Join-Path $PSScriptRoot 'MsiContractChecks.cs'))
    Run-Native $contractExe @('--prepare', $msi)
    Run-Native $contractExe @($msi)
    Run-Native $Wix @('msi', 'validate', $msi, '-sice', 'ICE20')
    if ($SignCertificateThumbprint) {
        $signParameters.FilePath = $msi
        if ((Set-AuthenticodeSignature @signParameters).Status -ne 'Valid') { throw 'MSI signing failed.' }
    }
    $files = @($msi, (Join-Path $OutputDirectory 'Service/ACTISync.Service.exe'), (Join-Path $OutputDirectory 'User/ACTISync.User.exe'), (Join-Path $OutputDirectory 'Updater/ACTISync.Updater.exe'), (Join-Path $OutputDirectory 'Tray/ACTISync.Tray.exe'))
    $manifest = foreach ($file in $files) {
        $item = Get-Item -LiteralPath $file
        [ordered]@{name=$item.Name; bytes=$item.Length; sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant(); signature=(Get-AuthenticodeSignature -LiteralPath $file).Status.ToString()}
    }
    $manifest | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $OutputDirectory 'manifest.json')
    Write-Output "Single installer (includes sync service, native user worker, tray and dedicated updater): $msi"
} finally { Pop-Location }
