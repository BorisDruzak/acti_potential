// Compile directly with Framework64/v4.0.30319/csc.exe. SDK-generated target
// framework attributes would hide the unmanaged CLR4 host compatibility bug.
// This fixture performs no networking, MSI actions or machine configuration.
using System;
using System.Net;
using System.Reflection;
using System.Runtime.Versioning;
using System.IO;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Text;
using System.Xml;

internal static class ColdClrHost
{
    private static int Main(string[] arguments)
    {
        if (arguments.Length != 1 && arguments.Length != 2) return Fail("Expected the managed installer-actions DLL path and optional packaged updater path.");
        if (Assembly.GetExecutingAssembly().GetCustomAttributes(typeof(TargetFrameworkAttribute), false).Length != 0)
            return Fail("The cold CLR host must not declare a target framework.");
        if (!string.IsNullOrEmpty(AppDomain.CurrentDomain.SetupInformation.TargetFrameworkName))
            return Fail("The cold CLR host unexpectedly has a target-framework identity.");

        try
        {
            // DTF 6 still selects the CLR through GetRequestedRuntimeInfo and
            // CorBindToRuntimeEx. A managed TLS test alone bypasses that path.
            var configPath = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(arguments[0])), "CustomAction.config");
            var config = new XmlDocument { XmlResolver = null };
            config.Load(configPath);
            var startup = config.SelectSingleNode("/configuration/startup") as XmlElement;
            if (startup == null || startup.GetAttribute("useLegacyV2RuntimeActivationPolicy") != "true")
                return Fail("DTF CLR4 activation requires an explicit legacy runtime activation policy.");
            var directory = new StringBuilder(260);
            var version = new StringBuilder(20);
            uint directoryLength, versionLength;
            var result = GetRequestedRuntimeInfo(arguments[0], null, configPath, 0, 0,
                directory, directory.Capacity, out directoryLength, version, version.Capacity, out versionLength);
            if (result < 0 || version.ToString() != "v4.0.30319")
                return Fail("DTF native runtime selection failed: 0x" + result.ToString("X8"));

            var assembly = Assembly.LoadFrom(arguments[0]);
            var policy = assembly.GetType("ACTISync.InstallerActions.InstallerTlsPolicy", false);
            var initialize = policy == null ? null : policy.GetMethod("Initialize", BindingFlags.Static | BindingFlags.NonPublic);
            if (initialize == null) return Fail("Installer TLS initialization is missing.");

            // Do not read ServicePointManager before this call: its static
            // initializer caches the old CLR4 compatibility defaults.
            initialize.Invoke(null, null);

            bool disableSystemDefault;
            if (!AppContext.TryGetSwitch("Switch.System.Net.DontEnableSystemDefaultTlsVersions", out disableSystemDefault) || disableSystemDefault)
                return Fail("Installer retained the legacy CLR TLS protocol defaults.");
            bool disableStrongCrypto;
            if (!AppContext.TryGetSwitch("Switch.System.Net.DontEnableSchUseStrongCrypto", out disableStrongCrypto) || disableStrongCrypto)
                return Fail("Installer failed to enable strong cryptography.");
            if (ServicePointManager.SecurityProtocol != SecurityProtocolType.SystemDefault)
                return Fail("Installer did not select the operating-system TLS policy.");

            if (arguments.Length == 2)
            {
                using (var embedded = assembly.GetManifestResourceStream("ACTISync.InstallerLifecycle.exe"))
                using (var published = File.OpenRead(arguments[1]))
                using (var hash = SHA256.Create())
                {
                    if (embedded == null || embedded.Length == 0 || embedded.Length != published.Length)
                        return Fail("Packaged lifecycle updater resource is missing or has a different length.");
                    var embeddedHash = Convert.ToBase64String(hash.ComputeHash(embedded));
                    var publishedHash = Convert.ToBase64String(hash.ComputeHash(published));
                    if (embeddedHash != publishedHash) return Fail("Packaged lifecycle updater resource differs from the published executable.");
                }
            }

            Console.WriteLine("DTF native CLR4 selection and cold host TLS policy tests passed; no network or MSI actions.");
            return 0;
        }
        catch (Exception)
        {
            return Fail("Unable to load or invoke installer TLS initialization.");
        }
    }

    [DllImport("mscoree.dll", CharSet = CharSet.Unicode)]
    private static extern int GetRequestedRuntimeInfo(string executable, string requestedVersion,
        string configurationFile, uint startupFlags, uint runtimeInfoFlags,
        StringBuilder directory, int directoryCapacity, out uint directoryLength,
        StringBuilder version, int versionCapacity, out uint versionLength);

    private static int Fail(string message)
    {
        Console.Error.WriteLine(message);
        return 1;
    }
}
