using ACTISync.Core;
using Microsoft.Extensions.Hosting;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;

namespace ACTISync.Service;

public sealed class SyncWorker(SyncEngine engine) : BackgroundService {
 protected override async Task ExecuteAsync(CancellationToken stoppingToken) {
  engine.Initialize();
  await Task.WhenAll(PipeLoop(stoppingToken), PollLoop(stoppingToken),AgentLoop(stoppingToken));
 }
 private async Task AgentLoop(CancellationToken ct) {
  using var timer=new PeriodicTimer(TimeSpan.FromSeconds(4));
  while(!ct.IsCancellationRequested) { await engine.AgentTick(ct); await timer.WaitForNextTickAsync(ct); }
 }
 private async Task PollLoop(CancellationToken ct) {
  using var timer = new PeriodicTimer(TimeSpan.FromSeconds(4));
  while (!ct.IsCancellationRequested) { await engine.Tick(ct); await timer.WaitForNextTickAsync(ct); }
 }
 private async Task PipeLoop(CancellationToken ct) {
  var acl = new PipeSecurity();
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
  acl.AddAccessRule(new PipeAccessRule(Security.ServiceSid, PipeAccessRights.FullControl, AccessControlType.Allow));
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.InteractiveSid, null), PipeAccessRights.ReadWrite, AccessControlType.Allow));
  while (!ct.IsCancellationRequested) {
   var pipe = NamedPipeServerStreamAcl.Create(Wire.PipeName, PipeDirection.InOut, 16, PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 4096, 4096, acl);
   try { await pipe.WaitForConnectionAsync(ct); } catch { pipe.Dispose(); throw; }
   _=Serve(pipe,ct);
  }
 }
 private async Task Serve(NamedPipeServerStream pipe,CancellationToken ct) {
  using(pipe) {
   try {
    string? sid = null;
    pipe.RunAsClient(() => { using var identity = WindowsIdentity.GetCurrent(true); sid = identity?.User?.Value; if (identity is null || !identity.IsAuthenticated || identity.IsAnonymous) sid = null; });
    if (sid is null || !sid.StartsWith("S-1-5-21-", StringComparison.Ordinal) && !sid.StartsWith("S-1-12-1-", StringComparison.Ordinal)) throw new UnauthorizedAccessException();
    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct); timeout.CancelAfter(TimeSpan.FromSeconds(45));
    // Bound the wire request before parsing; no arbitrary paths or origins are accepted.
    var bytes = new List<byte>(); var one = new byte[1];
    while (await pipe.ReadAsync(one, timeout.Token) != 0 && one[0] != 10) { bytes.Add(one[0]); if (bytes.Count > 4096) throw new InvalidDataException(); }
    var request = JsonSerializer.Deserialize<IpcRequest>(bytes.ToArray(), Wire.Json) ?? throw new InvalidDataException();
    var result = await engine.Handle(sid, request, timeout.Token);
    var encoded = JsonSerializer.SerializeToUtf8Bytes(result, Wire.Json); await pipe.WriteAsync(encoded, timeout.Token); await pipe.WriteAsync(new byte[]{10}, timeout.Token); await pipe.FlushAsync(timeout.Token);
   } catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException or OperationCanceledException) { /* A disconnected or malformed caller cannot terminate the service. */ }
  }
 }
}
