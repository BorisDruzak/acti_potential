using ACTISync.Core;
using ACTISync.Service;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;

internal static class TelemetryNegotiationChecks {
 private static void Check(bool ok,string message){if(!ok)throw new Exception(message);Console.WriteLine("PASS "+message);}
 internal static async Task Run() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-capabilities-"+Guid.NewGuid().ToString("N"));var config=new ClientConfig(new Uri("https://example.com/"));var sid=WindowsIdentity.GetCurrent().User!.Value;var id=Guid.NewGuid().ToString();
  using var server=new StrictServer();
  try {
   using(var setup=new SyncEngine(root,config,new HttpClient(server,false)))setup.Initialize();
   var work=Path.Combine(root,"work",sid,id);Directory.CreateDirectory(work);Directory.CreateDirectory(Path.Combine(root,"snapshots",id));
   var file=Path.Combine(work,"test.docx");var bytes=EngineChecks.Document("cached old native state");File.WriteAllBytes(file,bytes);
   var statePath=Path.Combine(root,"state",id+".json");DurableJson.Write(statePath,new SessionState{SessionId=id,Sid=sid,WorkingPath=file,FileName="test.docx",ServerOrigin=config.Origin.AbsoluteUri,ProtectedToken=Security.Protect("token"),ExpiresAt=DateTimeOffset.UtcNow.AddHours(1),Revision=1,AckHash=Convert.ToHexStringLower(SHA256.HashData(bytes))});
   using(var oldServer=new SyncEngine(root,config,new HttpClient(server,false))) {
    oldServer.Initialize();await oldServer.Tick(default);
    Check(server.Rejections==0 && !server.Shapes.Single(),"strict deployed legacy server accepts native heartbeat without optional telemetry");
   }
   server.Advertise=true;
   var changed=EngineChecks.Document("save concurrent with capability negotiation");File.WriteAllBytes(file,changed);
   var cached=DurableJson.Read<SessionState>(statePath)!;cached.RetryAt=DateTimeOffset.MinValue;DurableJson.Write(statePath,cached);
   using(var upgraded=new SyncEngine(root,config,new HttpClient(server,false))) {
    upgraded.Initialize();var upload=upgraded.Tick(default);await server.SaveStarted.Task.WaitAsync(TimeSpan.FromSeconds(4));
    await upgraded.LeaseTick(default);
    Check(!server.Shapes[^1],"cached pre-capability state sends legacy heartbeat before upgraded server advertisement");
    await upgraded.LeaseTick(default);
    Check(server.Shapes[^1] && server.Rejections==0,"independent lease heartbeat learns explicit telemetry capability without stale state overwrite");
    var inFlight=DurableJson.Read<SessionState>(statePath)!;
    Check(inFlight.Revision==1 && inFlight.Pending!=null && inFlight.LastAck is null,"capability discovery during transfer preserves the uncertain pending snapshot and prior ACK");
    server.SaveRelease.TrySetResult();await upload;
    var saved=DurableJson.Read<SessionState>(statePath)!;
    Check(saved.Revision==2 && saved.Pending is null && saved.AckHash==Convert.ToHexStringLower(SHA256.HashData(changed)),"upload ACK after concurrent capability negotiation retains exact latest revision and bytes");
   }
   using(var restarted=new SyncEngine(root,config,new HttpClient(server,false))) {
    restarted.Initialize();await restarted.LeaseTick(default);
    Check(server.Shapes[^1],"explicit telemetry capability survives native service restart");
    server.Advertise=false;var before=server.Shapes.Count;var saved=DurableJson.Read<SessionState>(statePath)!;
    await restarted.LeaseTick(default);
    var downgraded=DurableJson.Read<SessionState>(statePath)!;
    Check(server.Shapes.Skip(before).SequenceEqual(new[]{true,false}) && !downgraded.SupportsTelemetry,"independent lease retries deterministic schema rejection once with legacy body and invalidates persisted capability");
    Check(downgraded.AckHash==saved.AckHash && downgraded.Revision==saved.Revision && downgraded.ProtectedToken==saved.ProtectedToken,"rollback negotiation preserves current token, revision and acknowledged bytes");
   }
   var negotiated=DurableJson.Read<SessionState>(statePath)!;negotiated.SupportsTelemetry=true;negotiated.RetryAt=DateTimeOffset.MinValue;DurableJson.Write(statePath,negotiated);
   using(var rolledBack=new SyncEngine(root,config,new HttpClient(server,false))) {
    rolledBack.Initialize();var before=server.Shapes.Count;await rolledBack.Tick(default);
    var state=DurableJson.Read<SessionState>(statePath)!;
    Check(server.Shapes.Skip(before).SequenceEqual(new[]{true,false}) && !state.SupportsTelemetry && state.Status is not ("error" or "offline"),"restarted poll loop falls back from cached negotiation after strict server rollback without losing lease");
   }
   using(var persistedLegacy=new SyncEngine(root,config,new HttpClient(server,false))) {
    persistedLegacy.Initialize();var before=server.Shapes.Count;await persistedLegacy.LeaseTick(default);
    Check(server.Shapes.Skip(before).SequenceEqual(new[]{false}),"rollback fallback remains legacy across subsequent native restart");
   }
   foreach(var failure in new[]{HttpStatusCode.Forbidden,HttpStatusCode.ServiceUnavailable}) {
    var state=DurableJson.Read<SessionState>(statePath)!;state.SupportsTelemetry=true;DurableJson.Write(statePath,state);server.Failure=failure;
    using var denied=new SyncEngine(root,config,new HttpClient(server,false));denied.Initialize();var before=server.Shapes.Count;await denied.LeaseTick(default);
    Check(server.Shapes.Count==before+1 && DurableJson.Read<SessionState>(statePath)!.SupportsTelemetry,"auth or ambiguous server failure never replays heartbeat with legacy body");
   }
   server.Failure=null;server.NetworkFailure=true;
   using(var disconnected=new SyncEngine(root,config,new HttpClient(server,false))) {
    disconnected.Initialize();var before=server.Shapes.Count;await disconnected.LeaseTick(default);
    Check(server.Shapes.Count==before+1 && DurableJson.Read<SessionState>(statePath)!.SupportsTelemetry,"ambiguous network failure never triggers telemetry fallback or credential replay");
   }
  }finally{server.SaveRelease.TrySetResult();if(Directory.Exists(root))Directory.Delete(root,true);}
 }
 private sealed class StrictServer : HttpMessageHandler {
  internal bool Advertise;internal int Rejections;internal List<bool> Shapes=[];
  internal HttpStatusCode? Failure;internal bool NetworkFailure;
  internal TaskCompletionSource SaveStarted=new(TaskCreationOptions.RunContinuationsAsynchronously),SaveRelease=new(TaskCreationOptions.RunContinuationsAsynchronously);
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct) {
   if(request.RequestUri!.AbsolutePath.EndsWith("/save")) {
    var bytes=await request.Content!.ReadAsByteArrayAsync(ct);SaveStarted.TrySetResult();await SaveRelease.Task.WaitAsync(ct);
    return new(HttpStatusCode.OK){Content=JsonContent.Create(new{revision=2,sha256=Convert.ToHexStringLower(SHA256.HashData(bytes)),capabilities=new[]{"queue-telemetry-v1"}},options:Wire.Json)};
   }
   var body=await request.Content!.ReadFromJsonAsync<JsonElement>(Wire.Json,ct);
   var telemetry=body.TryGetProperty("pending_uploads",out _);Shapes.Add(telemetry);
   if(NetworkFailure)throw new HttpRequestException("simulated connection loss");
   if(Failure.HasValue)return new(Failure.Value);
   if(!Advertise && body.EnumerateObject().Any(p=>p.Name is not ("state" or "document_open"))) {Rejections++;return new(HttpStatusCode.UnprocessableEntity);}
   return new(HttpStatusCode.OK){Content=JsonContent.Create(new{lease_expires_at=DateTimeOffset.UtcNow.AddSeconds(90),capabilities=Advertise ? new[]{"queue-telemetry-v1","recovery-v1"} : Array.Empty<string>()},options:Wire.Json)};
  }
 }
}
