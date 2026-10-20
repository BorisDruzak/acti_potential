// Compile directly with Framework64/v4.0.30319/csc.exe. SDK-generated target
// framework attributes would hide the unmanaged CLR4 host compatibility bug.
// This fixture performs no networking, MSI actions or machine configuration.
using System;
using System.Net;
using System.Reflection;
using System.Runtime.Versioning;
using System.IO;
using System.Security.Cryptography;

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

            Console.WriteLine("Cold CLR4 host TLS policy test passed; no network or MSI actions.");
            return 0;
        }
        catch (Exception)
        {
            return Fail("Unable to load or invoke installer TLS initialization.");
        }
    }

    private static int Fail(string message)
    {
        Console.Error.WriteLine(message);
        return 1;
    }
}
