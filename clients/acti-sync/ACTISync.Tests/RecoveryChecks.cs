using ACTISync.Core;
using ACTISync.Service;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;

internal static class RecoveryChecks {
 private static void Check(bool ok,string message) {if(!ok)throw new Exception(message);Console.WriteLine("PASS "+message);}
 internal static async Task Run() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-recovery-"+Guid.NewGuid().ToString("N"));
  var config=new ClientConfig(new Uri("https://example.com/"));var sid=WindowsIdentity.GetCurrent().User!.Value;
  using var server=new RecoveryServer();var id=Guid.NewGuid().ToString();var connection=Guid.NewGuid().ToString();var command=Guid.NewGuid().ToString();
  var work=Path.Combine(root,"work",sid,id);var path=Path.Combine(work,"test.docx");var snapshot=Path.Combine(root,"snapshots",id,"pending.docx");
  var statePath=Path.Combine(root,"state",id+".json");var agentPath=Path.Combine(root,"agents",connection+".json");
  var bytes=EngineChecks.Document("local conflict preserved");var hash=Convert.ToHexStringLower(SHA256.HashData(bytes));
  void AddCommand(string current) {
   var agent=DurableJson.Read<AgentState>(agentPath)!;
   agent.Pending[current]=new(){Id=current,SessionId=id,Action="recover",ExpiresAt=DateTimeOffset.UtcNow.AddMinutes(1)};DurableJson.Write(agentPath,agent);
  }
  try {
   using(var setup=new SyncEngine(root,config,new HttpClient(server,false))) setup.Initialize();
   Directory.CreateDirectory(work);Directory.CreateDirectory(Path.GetDirectoryName(snapshot)!);File.WriteAllBytes(path,bytes);File.WriteAllBytes(snapshot,bytes);
   DurableJson.Write(statePath,new SessionState{SessionId=id,ConnectionId=connection,Sid=sid,WorkingPath=path,FileName="test.docx",ServerOrigin=config.Origin.AbsoluteUri,Status="conflict",ProtectedToken=Security.Protect("session-token"),Pending=new(){Path=snapshot,Hash=hash,ExpectedRevision=1},Revision=1,AckHash=new string('0',64)});
   DurableJson.Write(agentPath,new AgentState{ConnectionId=connection,Sid=sid,ServerOrigin=config.Origin.AbsoluteUri,ProtectedToken=Security.Protect("connection-token"),ExpiresAt=DateTimeOffset.UtcNow.AddHours(1)});AddCommand(command);
   File.WriteAllText(Path.Combine(work,"~$test.docx"),"owner");
   using(var open=new SyncEngine(root,config,new HttpClient(server,false))) {
    open.Initialize();
    var forged=await open.Handle(sid,new(){Action="command_result",CommandId=command,SessionId=id,Ok=true},default);
    Check(!forged.Ok,"generic native result cannot acknowledge recovery without durable copy and scoped server ACK");
    var refused=await open.Handle(sid,new(){Action="recover",CommandId=command,SessionId=id},default);
    Check(!refused.Ok && server.Recoveries==0 && DurableJson.Read<SessionState>(statePath)!.ErrorCode=="file_busy","recovery refuses open Word and publishes safe file_busy without HTTP");
   Check(DurableJson.Read<AgentState>(agentPath)!.Results.Any(r=>r.Id==command && r.Error=="file_busy"),"Word-open refusal relays only allowlisted file_busy command error");
   }
   File.Delete(Path.Combine(work,"~$test.docx"));command=Guid.NewGuid().ToString();AddCommand(command);
   var recovery=Path.Combine(root,"recovery",id,"recovered.docx");Directory.CreateDirectory(recovery);
   using(var failure=new SyncEngine(root,config,new HttpClient(server,false))) {
    failure.Initialize();var failed=await failure.Handle(sid,new(){Action="recover",CommandId=command,SessionId=id},default);
    Check(!failed.Ok && server.Recoveries==0 && File.Exists(path) && File.Exists(snapshot),"safe copy failure sends no recovery HTTP and preserves working/pending bytes");
   }
   Directory.Delete(recovery);var leftover=Path.Combine(Path.GetDirectoryName(recovery)!,"recovered.new");if(File.Exists(leftover))File.Delete(leftover);
   command=Guid.NewGuid().ToString();AddCommand(command);
   using(var engine=new SyncEngine(root,config,new HttpClient(server,false))) {
    engine.Initialize();
    Check(!(await engine.Handle(sid,new(){Action="recover",SessionId=id},default)).Ok,"recovery requires server-bound command identity");
    Check(!(await engine.Handle("S-1-5-21-1-2-3-1234",new(){Action="recover",SessionId=id,CommandId=command},default)).Ok,"recovery refuses another Windows SID");
    using(var writer=new FileStream(path,FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite)) {
     var busy=await engine.Handle(sid,new(){Action="recover",SessionId=id,CommandId=command},default);
     Check(!busy.Ok && server.Recoveries==0,"recovery refuses actual writable Word handle even without owner file");
    }
   }
   command=Guid.NewGuid().ToString();AddCommand(command);server.ExpectedCopy=recovery;
   File.WriteAllBytes(recovery,bytes);
   var expiredBinding=DurableJson.Read<SessionState>(statePath)!;expiredBinding.RecoveryCommandId=Guid.NewGuid().ToString();expiredBinding.RecoverySha256=hash;expiredBinding.RecoveryPending=true;DurableJson.Write(statePath,expiredBinding);
   using(var engine=new SyncEngine(root,config,new HttpClient(server,false))) {
    engine.Initialize();var lost=await engine.Handle(sid,new(){Action="recover",SessionId=id,CommandId=command},default);
    var pending=DurableJson.Read<SessionState>(statePath)!;
    Check(!lost.Ok && !pending.Finished && pending.RecoveryPending && pending.RecoveryCommandId==command && File.ReadAllBytes(recovery).SequenceEqual(bytes),"lost recovery ACK keeps verified durable copy, command identity and unfinished local state");
    Check(File.ReadAllBytes(path).SequenceEqual(bytes) && File.ReadAllBytes(snapshot).SequenceEqual(bytes),"recovery never removes working file or pending snapshot");
   }
   using(var restarted=new SyncEngine(root,config,new HttpClient(server,false))) {
    restarted.Initialize();await restarted.Tick(default);
    var done=DurableJson.Read<SessionState>(statePath)!;
    Check(done.Finished && !done.RecoveryPending && server.Recoveries==2 && server.CommandIds.Distinct().Count()==1 && server.Hashes.All(h=>h==hash),"restart retries same recovery command/hash and finishes only after scoped ACK");
    Check(File.Exists(path) && File.Exists(snapshot) && File.Exists(recovery),"successful recovery preserves every local source and snapshot");
    var reveal=(await restarted.Handle(sid,new(){Action="pulse"},default)).Sessions.Single();
    Check(reveal.RecoveryPath==recovery && reveal.RecoveryCommandId==command && reveal.RecoverySha256==hash,"lost ACK recovery returns only fixed service-owned verified copy through scoped pulse");
    Check((await restarted.Handle("S-1-5-21-1-2-3-1234",new(){Action="pulse"},default)).Sessions.Count==0,"recovery copy never crosses Windows SID boundary");
   }
   var interruptedReceipt=DurableJson.Read<AgentState>(agentPath)!;
   interruptedReceipt.Completed.Remove(command);interruptedReceipt.Pending[command]=new(){Id=command,SessionId=id,Action="recover",ExpiresAt=DateTimeOffset.UtcNow.AddSeconds(-1)};interruptedReceipt.Started.Add(command);DurableJson.Write(agentPath,interruptedReceipt);
   using(var delivery=new SyncEngine(root,config,new HttpClient(server,false))) {
    delivery.Initialize();
    Check((await delivery.Handle(sid,new(){Action="pulse"},default)).Sessions.Single().RecoveryPath==recovery,"restart preserves pending Explorer reveal until native acknowledgement");
    Check(!(await delivery.Handle(sid,new(){Action="recovery_revealed",CommandId=Guid.NewGuid().ToString(),SessionId=id},default)).Ok,"invented reveal receipt cannot clear durable recovery delivery");
    Check((await delivery.Handle(sid,new(){Action="recovery_revealed",CommandId=command,SessionId=id},default)).Ok && (await delivery.Handle(sid,new(){Action="pulse"},default)).Sessions.Count==0,"bound native reveal receipt clears delivery only after matching completed recovery");
   }
  } finally {if(Directory.Exists(root))Directory.Delete(root,true);}
 }
 private sealed class RecoveryServer : HttpMessageHandler {
  internal string ExpectedCopy="";internal int Recoveries;internal List<string> CommandIds=[],Hashes=[];
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct) {
   if(!request.RequestUri!.AbsolutePath.EndsWith("/recover")) return new(HttpStatusCode.OK){Content=JsonContent.Create(new{})};
   Check(request.Headers.Authorization?.Parameter=="session-token","recovery uses scoped session bearer");
   var body=await request.Content!.ReadFromJsonAsync<JsonElement>(Wire.Json,ct);
   Check(File.Exists(ExpectedCopy) && body.GetProperty("document_closed").GetBoolean(),"durable recovery copy precedes first server action");
   var hash=body.GetProperty("local_sha256").GetString()!;
   Check(Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(ExpectedCopy)))==hash,"wire recovery hash binds verified service-owned copy");
   Hashes.Add(hash);CommandIds.Add(body.GetProperty("command_id").GetString()!);Recoveries++;
   if(Recoveries==1)throw new HttpRequestException("simulated lost ACK");
   return new(HttpStatusCode.OK){Content=JsonContent.Create(new{session_id=request.RequestUri.Segments[^2].TrimEnd('/'),state="finished"},options:Wire.Json)};
  }
 }
}
