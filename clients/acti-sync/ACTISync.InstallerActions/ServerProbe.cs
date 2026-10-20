using System;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Threading;
using System.Threading.Tasks;

namespace ACTISync.InstallerActions
{
    internal static class ServerProbe
    {
        private const int TimeoutMilliseconds = 8000;

        internal static ProbeResult Check(Uri origin)
        {
            InstallerTlsPolicy.Initialize();
            var diagnostics = new ProbeDiagnostics();
            using (var cancellation = new CancellationTokenSource())
            {
                // DNS and platform certificate retrieval may outlast HttpWebRequest.Timeout.
                // Keep them off the MSI UI thread and bound the complete two-request operation.
                var work = Task.Run(() => ProbeCore(origin, cancellation.Token, diagnostics));
                if (work.Wait(TimeoutMilliseconds)) return new ProbeResult(work.Result.Failure, diagnostics);
                cancellation.Cancel();
                return new ProbeResult(ProbeFailure.Timeout, diagnostics);
            }
        }

        private static ProbeResult ProbeCore(Uri origin, CancellationToken cancellation, ProbeDiagnostics diagnostics)
        {
            try
            {
                var live = Fetch(new Uri(origin, "/api/v1/health/live"), false, cancellation, diagnostics);
                return live.Success ? Fetch(new Uri(origin, "/api/v1/health/ready"), true, cancellation, diagnostics) : live;
            }
            catch (OperationCanceledException) { return new ProbeResult(ProbeFailure.Timeout); }
            catch (Exception) { return new ProbeResult(ProbeFailure.InvalidServer); }
        }

        private static ProbeResult Fetch(Uri uri, bool ready, CancellationToken cancellation, ProbeDiagnostics diagnostics)
        {
            cancellation.ThrowIfCancellationRequested();
            var request = (HttpWebRequest)WebRequest.Create(uri);
            request.Method = "GET";
            request.Accept = "application/json";
            request.AllowAutoRedirect = false;
            request.UseDefaultCredentials = false;
            request.Credentials = null;
            request.CookieContainer = null;
            request.PreAuthenticate = false;
            request.Proxy = null;
            request.KeepAlive = false;
            request.AutomaticDecompression = DecompressionMethods.None;
            request.Timeout = TimeoutMilliseconds;
            request.ReadWriteTimeout = TimeoutMilliseconds;
            var checkRevocation = ServicePointManager.CheckCertificateRevocationList;
            var attempt = diagnostics.Begin(ready, ServicePointManager.SecurityProtocol, checkRevocation);
            var certificateRejected = false;
            request.ServerCertificateValidationCallback = (sender, certificate, chain, errors) =>
            {
                var trusted = ValidateMachineCertificateCore(certificate, chain, errors, checkRevocation, out var machineStatus, out var machineChecked);
                attempt.Certificate(errors, machineStatus, machineChecked);
                if (!trusted) certificateRejected = true;
                return trusted;
            };

            using (cancellation.Register(request.Abort))
            {
                try
                {
                    HttpWebResponse response;
                    try { response = (HttpWebResponse)request.GetResponse(); }
                    catch (WebException error) when (error.Response is HttpWebResponse)
                    {
                        response = (HttpWebResponse)error.Response;
                    }
                    using (response)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        var status = (int)response.StatusCode;
                        if ((status != 200 && status != 503) || response.ContentLength > 16 * 1024 ||
                            !string.Equals(response.ContentType?.Split(';')[0].Trim(), "application/json", StringComparison.OrdinalIgnoreCase))
                            return new ProbeResult(ProbeFailure.InvalidServer);
                        using (var stream = response.GetResponseStream())
                        {
                            if (stream == null) return new ProbeResult(ProbeFailure.InvalidServer);
                            return new ProbeResult(HealthPayload.Validate(HealthPayload.ReadBounded(stream), ready, status));
                        }
                    }
                }
                catch (WebException error)
                {
                    attempt.Transport(error.Status);
                    return new ProbeResult(cancellation.IsCancellationRequested ? ProbeFailure.Timeout :
                        certificateRejected ? ProbeFailure.Certificate : ProbeResult.Classify(error.Status));
                }
                catch (InvalidDataException) { return new ProbeResult(ProbeFailure.InvalidServer); }
                catch (IOException) { return new ProbeResult(cancellation.IsCancellationRequested ? ProbeFailure.Timeout : ProbeFailure.Connection); }
            }
        }

        internal static bool ValidateMachineCertificate(X509Certificate? certificate, X509Chain? chain, SslPolicyErrors errors, bool checkRevocation)
            => ValidateMachineCertificateCore(certificate, chain, errors, checkRevocation, out _, out _);

        private static bool ValidateMachineCertificateCore(X509Certificate? certificate, X509Chain? chain, SslPolicyErrors errors, bool checkRevocation,
            out X509ChainStatusFlags machineStatus, out bool machineChecked)
        {
            // The platform TLS check includes hostname and expiry; no TLS error is overridden.
            machineStatus = X509ChainStatusFlags.NoError;
            machineChecked = false;
            if (certificate == null) return false;
            try
            {
                using (var leaf = new X509Certificate2(certificate))
                using (var machineChain = new X509Chain(useMachineContext: true))
                {
                    machineChain.ChainPolicy.VerificationFlags = X509VerificationFlags.NoFlag;
                    // Match the request/agent default revocation policy; never ignore any returned TLS errors.
                    machineChain.ChainPolicy.RevocationMode = checkRevocation ? X509RevocationMode.Online : X509RevocationMode.NoCheck;
                    machineChain.ChainPolicy.RevocationFlag = X509RevocationFlag.ExcludeRoot;
                    machineChain.ChainPolicy.UrlRetrievalTimeout = TimeSpan.FromSeconds(2);
                    machineChain.ChainPolicy.ApplicationPolicy.Add(new Oid("1.3.6.1.5.5.7.3.1"));
                    if (chain != null)
                        foreach (X509ChainElement element in chain.ChainElements)
                            machineChain.ChainPolicy.ExtraStore.Add(element.Certificate);
                    var trusted = machineChain.Build(leaf);
                    machineChecked = true;
                    foreach (var status in machineChain.ChainStatus) machineStatus |= status.Status;
                    return errors == SslPolicyErrors.None && trusted;
                }
            }
            catch (CryptographicException) { return false; }
        }
    }
}
