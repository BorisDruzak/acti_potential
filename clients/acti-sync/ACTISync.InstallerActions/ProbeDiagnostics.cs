using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;

namespace ACTISync.InstallerActions
{
    internal sealed class ProbeDiagnostics
    {
        private readonly object gate = new object();
        private readonly List<ProbeAttempt> attempts = new List<ProbeAttempt>(2);

        internal ProbeAttempt Begin(bool ready, SecurityProtocolType protocols, bool revocation)
        {
            var attempt = new ProbeAttempt(ready, protocols, revocation);
            lock (gate) { if (attempts.Count < 2) attempts.Add(attempt); }
            return attempt;
        }

        internal IReadOnlyList<string> Snapshot()
        {
            var lines = new List<string>(8);
            lock (gate)
                foreach (var attempt in attempts) lines.AddRange(attempt.Snapshot());
            return lines.ToArray();
        }
    }

    internal sealed class ProbeAttempt
    {
        private readonly object gate = new object();
        private readonly bool ready;
        private readonly SecurityProtocolType protocols;
        private readonly bool revocation;
        private bool callback, machineChecked;
        private SslPolicyErrors errors;
        private X509ChainStatusFlags machineStatus;
        private WebExceptionStatus? transport;

        internal ProbeAttempt(bool ready, SecurityProtocolType protocols, bool revocation)
        {
            this.ready = ready; this.protocols = protocols; this.revocation = revocation;
        }

        internal void Certificate(SslPolicyErrors policyErrors, X509ChainStatusFlags chainStatus, bool checkedMachine = true)
        {
            lock (gate)
            {
                callback = true; errors |= policyErrors; machineStatus |= chainStatus; machineChecked |= checkedMachine;
            }
        }

        internal void Transport(WebExceptionStatus status) { lock (gate) { transport = status; } }

        internal string[] Snapshot()
        {
            lock (gate)
            {
                var prefix = "ACTI Sync check " + (ready ? "ready" : "live") + ": ";
                // Only locally generated enum/boolean codes enter this object. There is no
                // API for adding a URI, certificate, exception message or response payload.
                return new[]
                {
                    Bound(prefix + "tls=" + protocols + "; revocation=" + revocation),
                    Bound(prefix + "callback=" + callback + "; sslErrors=" + errors),
                    Bound(prefix + "machineChecked=" + machineChecked + "; machineChainCode=" + ((uint)machineStatus).ToString("X8") + "; machineChain=" + machineStatus),
                    Bound(prefix + "transport=" + (transport.HasValue ? transport.Value.ToString() : "none")),
                };
            }
        }

        private static string Bound(string line) => line.Length <= 256 ? line : line.Substring(0, 256);
    }
}
