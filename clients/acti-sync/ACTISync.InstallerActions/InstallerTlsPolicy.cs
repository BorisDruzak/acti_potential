using System;
using System.Net;

namespace ACTISync.InstallerActions
{
    internal static class InstallerTlsPolicy
    {
        internal static void Initialize()
        {
            // DTF's unmanaged CLR4 host has no target-framework identity. Its child
            // AppDomain can inherit SSL3/TLS1.0 even with the bundled runtime config.
            // Initialize before System.Net caches compatibility switches. These
            // settings live only in this short-lived custom-action AppDomain.
            AppContext.SetSwitch("Switch.System.Net.DontEnableSystemDefaultTlsVersions", false);
            AppContext.SetSwitch("Switch.System.Net.DontEnableSchUseStrongCrypto", false);
            ServicePointManager.SecurityProtocol = SecurityProtocolType.SystemDefault;
        }
    }
}
