using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using ACTISync.InstallerActions;

internal static class Program
{
    private static int Main()
    {
        try
        {
            OriginValidation();
            HealthValidation();
            ErrorClassification();
            TlsPolicyInitialization();
            SafeDiagnostics();
            CheckState();
            InstallerLifecycleTests.Run();
            MachineTrustValidation();
            UntrustedTlsProbe();
            ProbeTimeout();
            Console.WriteLine("Installer action tests passed.");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
    }

    private static void OriginValidation()
    {
        foreach (var item in new[]
        {
            (Input: "server.example", Expected: "https://server.example"),
            (Input: " HTTPS://SERVER.EXAMPLE/ ", Expected: "https://server.example"),
            (Input: "server.example:8443", Expected: "https://server.example:8443"),
            (Input: "https://192.0.2.1:8443/", Expected: "https://192.0.2.1:8443"),
            (Input: "https://пример.рф/", Expected: "https://xn--e1afmkfd.xn--p1ai"),
            (Input: "https://[2001:db8::1]:8443", Expected: "https://[2001:db8::1]:8443"),
        })
        {
            Assert(ServerOrigin.TryNormalize(item.Input, out var actual), "Valid HTTPS server rejected: " + item.Input);
            Assert(actual == item.Expected, "Server authority normalization failed: " + item.Input);
        }

        foreach (var input in new[]
        {
            "", " ", "http://server.example", "ftp://server.example", "https://user:secret@server.example",
            "https://server.example/path", "https://server.example/?q=x", "https://server.example/#part",
            "https://server.example?", "https://server.example#", "https://server.example\\path",
            "https://server.example//", "https://server.example/./", "https://server.example/%2e",
            "https://server.example:0", "https://server.example:65536", "https:///server.example",
            "https://server .example", "https://server.example\n", "https://server.example/%2f",
            "//server.example", "https://", "https://server.example/@user", "https://server.example:abc",
            "https://server.example\t/path", "https://server.example:443:", "https://%73erver.example",
        })
        {
            Assert(!ServerOrigin.TryNormalize(input, out _), "Unsafe or malformed server accepted: " + input);
        }
    }

    internal static void Assert(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static ProbeFailure Parse(string json, bool ready = false, int status = 200) =>
        HealthPayload.Validate(Encoding.UTF8.GetBytes(json), ready, status);

    private static void HealthValidation()
    {
        Assert(Parse("{\"status\":\"live\"}") == ProbeFailure.None, "ACTI liveness rejected.");
        Assert(Parse("{\"status\":\"ready\",\"checks\":{\"database\":true,\"redis\":true,\"storage\":true}}", true) == ProbeFailure.None,
            "Healthy ACTI readiness rejected.");
        foreach (var invalid in new[] { "", "<html>OK</html>", "{}", "null", "[]", "{\"status\":\"ok\"}", "{\"status\":true}", "{\"status\":\"live\",\"status\":\"live\"}" })
            Assert(Parse(invalid) == ProbeFailure.InvalidServer, "Invalid liveness accepted.");
        foreach (var invalid in new[]
        {
            "{\"status\":\"ready\"}", "{\"status\":\"ready\",\"checks\":{\"database\":true,\"redis\":true}}",
            "{\"status\":\"ready\",\"checks\":{\"database\":\"true\",\"redis\":true,\"storage\":true}}",
            "{\"status\":\"live\",\"checks\":{\"database\":true,\"redis\":true,\"storage\":true}}",
        })
            Assert(Parse(invalid, true) == ProbeFailure.InvalidServer, "Invalid readiness accepted.");
        Assert(Parse("{\"status\":\"ready\",\"checks\":{\"database\":true,\"redis\":false,\"storage\":true}}", true) == ProbeFailure.Unhealthy,
            "Unavailable ACTI dependency accepted.");
        Assert(Parse("{\"status\":\"unavailable\",\"checks\":{\"database\":false,\"redis\":true,\"storage\":true}}", true, 503) == ProbeFailure.Unhealthy,
            "ACTI unavailable response misclassified.");
        Assert(Parse("{\"status\":\"live\"}", false, 302) == ProbeFailure.InvalidServer, "Redirect accepted.");
        Assert(Parse("{\"status\":\"live\"} trailing") == ProbeFailure.InvalidServer, "Trailing malformed data accepted.");
        using (var limit = new MemoryStream(new byte[16 * 1024]))
            Assert(HealthPayload.ReadBounded(limit).Length == 16 * 1024, "Bounded response was truncated.");
        using (var tooLarge = new MemoryStream(new byte[16 * 1024 + 1]))
        {
            var rejected = false;
            try { HealthPayload.ReadBounded(tooLarge); }
            catch (InvalidDataException) { rejected = true; }
            Assert(rejected, "Oversized health response accepted.");
        }
    }

    private static void ErrorClassification()
    {
        foreach (var item in new[]
        {
            (WebExceptionStatus.TrustFailure, ProbeFailure.Certificate),
            (WebExceptionStatus.SecureChannelFailure, ProbeFailure.TlsNegotiation),
            (WebExceptionStatus.NameResolutionFailure, ProbeFailure.NameResolution),
            (WebExceptionStatus.ProxyNameResolutionFailure, ProbeFailure.NameResolution),
            (WebExceptionStatus.Timeout, ProbeFailure.Timeout),
            (WebExceptionStatus.ConnectFailure, ProbeFailure.Connection),
            (WebExceptionStatus.ConnectionClosed, ProbeFailure.Connection),
            (WebExceptionStatus.ProtocolError, ProbeFailure.InvalidServer),
        })
            Assert(ProbeResult.Classify(item.Item1) == item.Item2, "Network error misclassified: " + item.Item1);
        foreach (ProbeFailure failure in Enum.GetValues(typeof(ProbeFailure)))
            Assert(!string.IsNullOrWhiteSpace(new ProbeResult(failure).Message), "Missing user-facing check result.");
        Assert(ProbeResult.Classify(WebExceptionStatus.SecureChannelFailure, true) == ProbeFailure.Certificate,
            "A rejected certificate was reported as a TLS negotiation failure.");
    }

    private static void TlsPolicyInitialization()
    {
        var previous = ServicePointManager.SecurityProtocol;
        try
        {
            // Simulate the legacy defaults inherited by an unmanaged CLR4 host.
            AppContext.SetSwitch("Switch.System.Net.DontEnableSystemDefaultTlsVersions", true);
            AppContext.SetSwitch("Switch.System.Net.DontEnableSchUseStrongCrypto", true);
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            InstallerTlsPolicy.Initialize();
            Assert(ServicePointManager.SecurityProtocol == SecurityProtocolType.SystemDefault, "Installer retained the host's fixed TLS protocol policy.");
            Assert(AppContext.TryGetSwitch("Switch.System.Net.DontEnableSystemDefaultTlsVersions", out var noSystemDefault) && !noSystemDefault,
                "Installer retained legacy CLR TLS defaults.");
            Assert(AppContext.TryGetSwitch("Switch.System.Net.DontEnableSchUseStrongCrypto", out var noStrongCrypto) && !noStrongCrypto,
                "Installer failed to enable strong cryptography.");
        }
        finally { ServicePointManager.SecurityProtocol = previous; }
    }

    private static void SafeDiagnostics()
    {
        var diagnostics = new ProbeDiagnostics();
        for (var index = 0; index < 100; index++)
        {
            var attempt = diagnostics.Begin(index % 2 == 1, SecurityProtocolType.Tls12, false);
            attempt.Certificate(SslPolicyErrors.RemoteCertificateChainErrors, X509ChainStatusFlags.UntrustedRoot);
            attempt.Transport(WebExceptionStatus.TrustFailure);
        }
        var lines = diagnostics.Snapshot();
        Assert(lines.Count > 0 && lines.Count <= 8, "Installer diagnostics are missing or unbounded.");
        foreach (var line in lines)
        {
            Assert(line.Length <= 256 && !line.Contains("https://") && !line.Contains("CN=") && !line.Contains("secret"),
                "Installer diagnostics contain private input or unbounded data.");
        }
        Assert(string.Join(" ", lines).Contains("UntrustedRoot") && string.Join(" ", lines).Contains("TrustFailure"),
            "Installer diagnostics lost the certificate or transport failure code.");
    }

    private static void CheckState()
    {
        var state = new Dictionary<string, string> { ["SERVERORIGIN"] = "server.example", ["ACTICHECKOK"] = "1", ["ACTICHECKMESSAGE"] = "old" };
        Action<string, string> set = (key, value) => state[key] = value;
        var calls = 0;
        InstallerCheck.Run(() => state["SERVERORIGIN"], set, uri =>
        {
            calls++;
            Assert(state["ACTICHECKOK"] == "" && state["ACTICHECKMESSAGE"] == "", "Previous check success survived into a new check.");
            Assert(uri.AbsoluteUri == "https://server.example/", "Probe received the wrong origin.");
            return new ProbeResult(ProbeFailure.None);
        });
        Assert(calls == 1 && state["ACTICHECKOK"] == "1", "Successful live check not persisted.");
        Assert(state["SERVERORIGIN"] == "https://server.example", "Normalized server not persisted.");
        InstallerCheck.Run(() => state["SERVERORIGIN"], set, _ => new ProbeResult(ProbeFailure.Unhealthy));
        Assert(state["ACTICHECKOK"] == "" && state["ACTICHECKMESSAGE"].Contains("не готов"), "Stale success survived failed health check.");
        state["SERVERORIGIN"] = "http://server.example";
        InstallerCheck.Run(() => state["SERVERORIGIN"], set, _ => throw new InvalidOperationException("Must not probe invalid input."));
        Assert(state["ACTICHECKOK"] == "" && state["ACTICHECKMESSAGE"].Contains("HTTPS"), "Invalid server input was not explained.");
        state["SERVERORIGIN"] = "server.example";
        InstallerCheck.Run(() => state["SERVERORIGIN"], set, _ => throw new InvalidOperationException("secret details"));
        Assert(state["ACTICHECKOK"] == "" && !state["ACTICHECKMESSAGE"].Contains("secret"), "Exception leaked into installer UI.");
    }

    private static void MachineTrustValidation()
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest("CN=server.example", key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var untrusted = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(1));
        Assert(!ServerProbe.ValidateMachineCertificate(untrusted, null, SslPolicyErrors.None, false), "TLS accepted a certificate without machine trust.");
        Assert(!ServerProbe.ValidateMachineCertificate(untrusted, null, SslPolicyErrors.RemoteCertificateNameMismatch, false), "TLS name mismatch accepted.");
        Assert(!ServerProbe.ValidateMachineCertificate(null, null, SslPolicyErrors.RemoteCertificateNotAvailable, false), "Missing TLS certificate accepted.");
    }

    private static void ProbeTimeout()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        using var cancellation = new CancellationTokenSource();
        var stalledServer = Task.Run(async () =>
        {
            try
            {
                using var socket = await listener.AcceptTcpClientAsync(cancellation.Token);
                // Accept TCP but never complete TLS: the MSI check must release its caller.
                await Task.Delay(Timeout.Infinite, cancellation.Token);
            }
            catch (OperationCanceledException) { }
        });
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        var elapsed = Stopwatch.StartNew();
        var result = ServerProbe.Check(new Uri("https://127.0.0.1:" + port));
        cancellation.Cancel();
        Assert(result.Failure == ProbeFailure.Timeout, "Stalled TLS server did not report a timeout.");
        Assert(elapsed.Elapsed < TimeSpan.FromSeconds(12), "Connection check exceeded its bounded UI wait.");
        Assert(stalledServer.Wait(TimeSpan.FromSeconds(2)), "Timeout test fixture did not stop.");
    }

    private static void UntrustedTlsProbe()
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest("CN=127.0.0.1", key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        var names = new SubjectAlternativeNameBuilder();
        names.AddIpAddress(IPAddress.Loopback);
        request.CertificateExtensions.Add(names.Build());
        using var generated = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(1));
        // Schannel requires a non-ephemeral server key handle. Loading this generated PFX does
        // not add a certificate to any store; disposing the certificate removes its temporary key.
        using var certificate = new X509Certificate2(generated.Export(X509ContentType.Pfx), string.Empty, X509KeyStorageFlags.UserKeySet);
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var handshake = Task.Run(async () =>
        {
            try
            {
                using var socket = await listener.AcceptTcpClientAsync(cancellation.Token);
                using var tls = new SslStream(socket.GetStream());
                await tls.AuthenticateAsServerAsync(new SslServerAuthenticationOptions
                {
                    ServerCertificate = certificate, EnabledSslProtocols = SslProtocols.Tls12,
                }, cancellation.Token);
            }
            catch (Exception error) when (error is AuthenticationException || error is IOException || error is OperationCanceledException) { }
        });
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        var result = ServerProbe.Check(new Uri("https://127.0.0.1:" + port));
        cancellation.Cancel();
        Assert(result.Failure == ProbeFailure.Certificate, "Real TLS check failed to report untrusted machine certificate: " + result.Failure);
        var diagnostics = string.Join(" ", result.Diagnostics);
        Assert(diagnostics.Contains("RemoteCertificateChainErrors") && diagnostics.Contains("UntrustedRoot"),
            "Real TLS failure lost its platform or machine-chain diagnostic code.");
        Assert(!diagnostics.Contains("127.0.0.1") && !diagnostics.Contains("CN=") && !diagnostics.Contains(certificate.Thumbprint),
            "Real TLS diagnostics leaked endpoint or certificate data.");
        Assert(handshake.Wait(TimeSpan.FromSeconds(2)), "TLS test fixture did not stop.");
    }
}
