// Compile directly with Framework64/v4.0.30319/csc.exe. SDK-generated target
// framework attributes would hide the unmanaged CLR4 host compatibility bug.
// This fixture performs no networking, MSI actions or machine configuration.
using System;
using System.Net;
using System.Reflection;
using System.Runtime.Versioning;

internal static class ColdClrHost
{
    private static int Main(string[] arguments)
    {
        if (arguments.Length != 1) return Fail("Expected the managed installer-actions DLL path.");
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
