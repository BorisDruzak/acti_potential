using ACTISync.Core;
using System.Net.Http.Json;
using System.Collections.Concurrent;
using System.Net;

namespace ACTISync.Service;

public sealed partial class SyncEngine {
 private readonly ConcurrentDictionary<(string Session,string Origin),bool> telemetryCapabilities=[];
 private bool SupportsTelemetry(SessionState s)=>telemetryCapabilities.TryGetValue((s.SessionId,s.ServerOrigin),out var enabled) ? enabled : s.SupportsTelemetry;
 private void LearnCapabilities(SessionState s,ServerSession? response) {
  if(response?.Capabilities?.Contains("queue-telemetry-v1",StringComparer.Ordinal)==true) {s.SupportsTelemetry=true;telemetryCapabilities[(s.SessionId,s.ServerOrigin)]=true;}
 }
 // Buffering is intentional: the linked deadline covers headers AND the full
 // response body. ResponseHeadersRead alone would leave body reads unbounded.
 private async Task<HttpResponseMessage> SendBounded(HttpRequestMessage request,CancellationToken ct,bool transfer=false) {
  if(transfer) await transfers.WaitAsync(ct);
  try {
   using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(RequestTimeout);
   return await http.SendAsync(request,HttpCompletionOption.ResponseContentRead,deadline.Token);
  } finally { if(transfer) transfers.Release(); }
 }
 private static string HeartbeatState(SessionState s)=>s.Status is "conflict" ? "conflict" : s.Status is "error" or "reauth" ? "error" : s.Opened ? "editing" : "opening";
 private static object LegacyTelemetry(SessionState s)=>new{state=HeartbeatState(s),document_open=s.DocumentOpen};
 private object Telemetry(SessionState s) {
  var state=HeartbeatState(s);
  return new {
  state,
  document_open=s.DocumentOpen,
  pending_uploads=(s.Pending is null ? 0 : 1)+(s.NextPending is null ? 0 : 1),
  error_code=SafeErrorCode(s.ErrorCode)
 };
 }
 private async Task PublishTelemetryCapability(SessionState snapshot,bool enabled,CancellationToken ct) {
  telemetryCapabilities[(snapshot.SessionId,snapshot.ServerOrigin)]=enabled;
  var gate=SessionGate(snapshot.SessionId);
  if(await gate.WaitAsync(0,ct))try {
   if(sessions.TryGetValue(snapshot.SessionId,out var current) && current.ServerOrigin==snapshot.ServerOrigin && current.SupportsTelemetry!=enabled) {current.SupportsTelemetry=enabled;Persist(current);}
  }finally{gate.Release();}
 }
 private async Task<HttpResponseMessage> SendHeartbeat(SessionState s,CancellationToken ct,bool ownsSession=false) {
  var extended=SupportsTelemetry(s);
  var response=await Send(s,HttpMethod.Post,"heartbeat",JsonContent.Create(extended ? Telemetry(s) : LegacyTelemetry(s),options:Wire.Json),ct);
  // A strict rolled-back server rejects additive fields before mutating the
  // lease. Only this deterministic validation response permits one legacy retry;
  // transport failures, authentication failures and other statuses never replay.
  if(!extended || response.StatusCode!=HttpStatusCode.UnprocessableEntity)return response;
  response.Dispose();
  if(ownsSession) {telemetryCapabilities[(s.SessionId,s.ServerOrigin)]=false;s.SupportsTelemetry=false;Persist(s);}
  else await PublishTelemetryCapability(s,false,ct);
  return await Send(s,HttpMethod.Post,"heartbeat",JsonContent.Create(LegacyTelemetry(s),options:Wire.Json),ct);
 }
 private static string? SafeErrorCode(string? code)=>code is "network" or "conflict" or "reauth" or "quota" or "file_busy" or "word_missing" or "local_io" or "server_rejected" ? code : null;
 // Independent of Tick/IPC serialization. A slow save must never prevent lease
 // renewal, including the slow save's own lease. Read immutable published state;
 // never write a stale heartbeat snapshot over a later upload acknowledgement.
 public Task LeaseTick(CancellationToken ct)=>Task.WhenAll(heartbeatStates.Values.Where(s=>!s.Finished && config.Matches(s.ServerOrigin)).Select(s=>LeaseSession(s,ct)));
 private async Task LeaseSession(SessionState snapshot,CancellationToken ct) {
  try {
   using var response=await SendHeartbeat(snapshot,ct);
   await Ensure(response);
   var alive=await response.Content.ReadFromJsonAsync<ServerSession>(Wire.Json,ct);
   if(alive?.Capabilities?.Contains("queue-telemetry-v1",StringComparer.Ordinal)==true) {
    // Publish only metadata onto current locked state; never replace a session
    // with the immutable snapshot captured before this request.
    await PublishTelemetryCapability(snapshot,true,ct);
   }
  } catch(Exception e) when(e is HttpRequestException or IOException or OperationCanceledException or UnauthorizedAccessException or System.Security.Cryptography.CryptographicException or System.Text.Json.JsonException or InvalidOperationException) { }
 }
}
