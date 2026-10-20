using System;

namespace ACTISync.InstallerActions
{
    internal static class InstallerCheck
    {
        internal static ProbeResult Run(Func<string> getOrigin, Action<string, string> setProperty, Func<Uri, ProbeResult> probe)
        {
            // Always invalidate the previous result before reading or checking the current input.
            setProperty("ACTICHECKOK", string.Empty);
            setProperty("ACTICHECKMESSAGE", string.Empty);
            ProbeResult result;
            try
            {
                if (!ServerOrigin.TryNormalize(getOrigin(), out var origin))
                    result = new ProbeResult(ProbeFailure.InvalidUrl);
                else
                {
                    setProperty("SERVERORIGIN", origin);
                    result = probe(new Uri(origin));
                }
            }
            catch (Exception)
            {
                // Do not display or log URLs, exception text, credentials or server response bodies.
                result = new ProbeResult(ProbeFailure.InvalidServer);
            }
            setProperty("ACTICHECKMESSAGE", result.Message);
            if (result.Success) setProperty("ACTICHECKOK", "1");
            return result;
        }
    }
}
