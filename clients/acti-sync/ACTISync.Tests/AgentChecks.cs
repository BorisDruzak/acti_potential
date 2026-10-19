using ACTISync.Core;
using ACTISync.Service;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;

internal static class AgentChecks {
 private static void Check(bool condition,string message) { if(!condition) throw new Exception(message); Console.WriteLine("PASS "+message); }
 internal static async Task Run() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-agent-"+Guid.NewGuid().ToString("N"));
  var sid=WindowsIdentity.GetCurrent().User!.Value; var other="S-1-5-21-1-2-3-9999";
  var now=DateTimeOffset.UtcNow; var config=new ClientConfig(new Uri("https://example.com/"));
  using var server=new RelayServer();
  Check(AgentPresence.IsFresh(now,now.AddSeconds(15)) && !AgentPresence.IsFresh(now,now.AddSeconds(15.001)) && !AgentPresence.IsFresh(null,now),"user presence ends after 15 seconds and never exists without pulse");
  try {
   string session;
   using(var engine=new SyncEngine(root,config,new HttpClient(server,false)){Clock=()=>now}) {
    engine.Initialize();
    var bad=await engine.Handle(sid,new(){Action="connect",Ticket="bad"},default);
    Check(!bad.Ok && server.Requests==0,"connect rejects malformed ticket before HTTP");
    var connected=await engine.Handle(sid,new(){Action="connect",Ticket=new string('c',43)},default);
    Check(connected.Ok && (await engine.Handle(sid,new(),default)).Sessions.Count==0,"agent connects with zero documents");
    var agentPath=Path.Combine(root,"agents",server.ConnectionId+".json");
    Check(!File.ReadAllText(agentPath).Contains(server.Token,StringComparison.Ordinal),"connection credential persists only as DPAPI with SID and origin");
    await engine.AgentTick(default); Check(server.Heartbeats==0,"background service cannot claim interactive presence");
    await engine.Handle(other,new(){Action="pulse"},default); await engine.AgentTick(default);
    Check(server.Heartbeats==0,"another Windows SID pulse cannot mark connection online");
    await engine.Handle(sid,new(){Action="pulse"},default); await engine.AgentTick(default);
    Check(server.Heartbeats==1 && server.Renews==1,"live user pulse permits heartbeat and scoped connection renewal");
    now=now.AddSeconds(16); await engine.AgentTick(default); Check(server.Heartbeats==1,"dead user component stops connection heartbeats");
    var launch=await engine.Handle(sid,new(){Action="launch",Ticket=new string('l',43)},default); session=launch.Session!.SessionId;
    var command=new AgentCommand{Id=Guid.NewGuid().ToString(),SessionId=session,Action="reveal",ExpiresAt=now.AddSeconds(60)}; server.Commands=[command];
    await engine.Handle(sid,new(){Action="pulse"},default); await engine.AgentTick(default);
    var pending=await engine.Handle(sid,new(){Action="pulse"},default);
    Check(pending.Commands.Single().Id==command.Id,"browser command relays to bound interactive component");
    Check(!(await engine.Handle(other,new(){Action="command_start",CommandId=command.Id,SessionId=session},default)).Ok,"command cannot cross local SID boundary");
    Check(!(await engine.Handle(sid,new(){Action="command_start",CommandId=Guid.NewGuid().ToString(),SessionId=session},default)).Ok,"invented command cannot access a local working path");
    var started=await engine.Handle(sid,new(){Action="command_start",CommandId=command.Id,SessionId=session},default);
    Check(started.Ok && started.Session!.WorkingPath==launch.Session.WorkingPath,"validated reveal obtains only service-owned session path");
    Check(!(await engine.Handle(sid,new(){Action="command_start",CommandId=command.Id,SessionId=session},default)).Ok,"duplicate native command is not dispatched twice");
    await engine.Handle(sid,new(){Action="command_result",CommandId=command.Id,SessionId=session,Ok=true},default);
    await engine.AgentTick(default); Check(server.Results.Any(r=>r.Id==command.Id && r.Ok),"native command result relays to originating browser connection");
    await engine.AgentTick(default); pending=await engine.Handle(sid,new(){Action="pulse"},default);
    Check(pending.Commands.Count==0,"replayed acknowledged command is deduplicated durably");
    var expired=new AgentCommand{Id=Guid.NewGuid().ToString(),SessionId=session,Action="retry",ExpiresAt=now.AddSeconds(1)}; server.Commands=[expired];
    await engine.AgentTick(default); now=now.AddSeconds(2);
    Check((await engine.Handle(sid,new(){Action="pulse"},default)).Commands.Count==0 && !(await engine.Handle(sid,new(){Action="retry",CommandId=expired.Id,SessionId=session},default)).Ok,"expired queued command cannot execute before next server heartbeat");
    var uncertain=new AgentCommand{Id=Guid.NewGuid().ToString(),SessionId=session,Action="open",ExpiresAt=now.AddSeconds(60)}; server.Commands=[uncertain];
    await engine.AgentTick(default); await engine.Handle(sid,new(){Action="command_start",CommandId=uncertain.Id,SessionId=session},default);
   }
   var persistedPath=Path.Combine(root,"agents",server.ConnectionId+".json");
   var persisted=DurableJson.Read<AgentState>(persistedPath)!;
   var oldCommand=new AgentCommand{Id=Guid.NewGuid().ToString(),SessionId=session,Action="finish",ExpiresAt=DateTimeOffset.UtcNow.AddSeconds(-1)};
   persisted.Pending[oldCommand.Id]=oldCommand; DurableJson.Write(persistedPath,persisted);
   var before=server.Heartbeats;
   using(var recovered=new SyncEngine(root,config,new HttpClient(server,false))) {
    recovered.Initialize(); await recovered.AgentTick(default); Check(server.Heartbeats==before,"restart does not recover stale interactive presence");
    var pulse=await recovered.Handle(sid,new(){Action="pulse"},default);
    Check(pulse.Commands.Count==0 && !DurableJson.Read<AgentState>(persistedPath)!.Pending.ContainsKey(oldCommand.Id),"restart prunes expired commands and preserves native receipt without reopening Word");
    await recovered.AgentTick(default);
    Check(server.Results.Any(r=>!r.Ok && r.Id==server.Commands.Single().Id),"uncertain native action reports failure after service restart");
   }
   before=server.Requests;
   using(var moved=new SyncEngine(root,new ClientConfig(new Uri("https://other.example.com/")),new HttpClient(server,false))) {
    moved.Initialize(); await moved.Handle(sid,new(){Action="pulse"},default); await moved.AgentTick(default);
    Check(server.Requests==before,"changed installed origin never relays old connection token");
   }
   Console.WriteLine("ALL HEADLESS AGENT CHECKS PASSED");
  } finally { Directory.Delete(root,true); }
 }
 private sealed class RelayServer : HttpMessageHandler {
  public string ConnectionId=Guid.NewGuid().ToString(),Token="test-agent-connection-token";
  private readonly string session=Guid.NewGuid().ToString(); private readonly byte[] document=EngineChecks.Document("initial");
  public int Requests,Heartbeats,Renews; public List<AgentCommand> Commands=[]; public List<CommandResult> Results=[];
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct) {
   Requests++; var path=request.RequestUri!.AbsolutePath;
   object Credentials(bool shortLife=false)=>new{connection_id=ConnectionId,token=Token,expires_at=DateTimeOffset.UtcNow.AddMinutes(shortLife ? 1 : 15)};
   HttpResponseMessage Json(object body)=>new(HttpStatusCode.OK){Content=JsonContent.Create(body,options:Wire.Json)};
   if(path.EndsWith("agent/exchange")) return Json(Credentials(true));
   if(path.EndsWith("/exchange")) return Json(new{session_id=session,token="document-scoped-token",file_id=Guid.NewGuid().ToString(),filename="test.docx",revision=1,sha256=Convert.ToHexStringLower(SHA256.HashData(document)),expires_at=DateTimeOffset.UtcNow.AddMinutes(15),lease_expires_at=DateTimeOffset.UtcNow.AddSeconds(90),agent=Credentials()});
   if(path.Contains("/agents/")) {
    Check(request.Headers.Authorization?.Parameter==Token,"presence relay uses only connection bearer");
    if(path.EndsWith("/renew")) { Renews++; Token="rotated-agent-connection-token"; return Json(Credentials()); }
    Heartbeats++; var body=await request.Content!.ReadFromJsonAsync<JsonElement>(Wire.Json,ct);
    Results.AddRange(JsonSerializer.Deserialize<List<CommandResult>>(body.GetProperty("command_results"),Wire.Json)!);
    return Json(new{commands=Commands});
   }
   Check(request.Headers.Authorization?.Parameter=="document-scoped-token","document download retains separate document bearer");
   return new(HttpStatusCode.OK){Content=new ByteArrayContent(document)};
  }
 }
}
