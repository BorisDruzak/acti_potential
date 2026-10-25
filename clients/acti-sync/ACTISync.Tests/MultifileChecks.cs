using ACTISync.Core;
using ACTISync.Service;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Security.Principal;

internal static class MultifileChecks {
 private static void Check(bool ok,string message) { if(!ok) throw new Exception(message); Console.WriteLine("PASS "+message); }
 internal static async Task Run() {
  foreach(var count in new[]{3,10,20}) await Isolation(count);
 }
 private static async Task Isolation(int count) {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-multi-"+Guid.NewGuid().ToString("N"));
  var config=new ClientConfig(new Uri("https://example.com/")); var sid=WindowsIdentity.GetCurrent().User!.Value;
  using var server=new MultiServer();
  try {
   using(var initial=new SyncEngine(root,config,new HttpClient(server,false))) initial.Initialize();
   var ids=Enumerable.Range(0,count).Select(_=>Guid.NewGuid().ToString()).ToArray(); server.Stalled=ids[0];
   foreach(var id in ids) {
    var work=Path.Combine(root,"work",sid,id); Directory.CreateDirectory(work);Directory.CreateDirectory(Path.Combine(root,"snapshots",id));
    var path=Path.Combine(work,"test.docx");File.WriteAllBytes(path,EngineChecks.Document("changed "+id));File.WriteAllText(Path.Combine(work,"~$test.docx"),"owner");
    DurableJson.Write(Path.Combine(root,"state",id+".json"),new SessionState{SupportsTelemetry=true,SessionId=id,Sid=sid,ServerOrigin=config.Origin.AbsoluteUri,ProtectedToken=Security.Protect("token"),WorkingPath=path,FileName="test.docx",FileId=id,Revision=1,AckHash=new string('0',64),Opened=true,DocumentOpen=true,ExpiresAt=DateTimeOffset.UtcNow.AddHours(1)});
   }
   using(var engine=new SyncEngine(root,config,new HttpClient(server,false))) {engine.Initialize();
   using var cancellation=new CancellationTokenSource(TimeSpan.FromSeconds(15));
   var tick=engine.Tick(cancellation.Token);
   await server.Started.Task.WaitAsync(TimeSpan.FromSeconds(4));
   var list=engine.Handle(sid,new(){Action="list"},cancellation.Token);
   var pulse=engine.Handle(sid,new(){Action="pulse"},cancellation.Token);
   var independent=Task.WhenAll(list,pulse);
   Check(await Task.WhenAny(independent,Task.Delay(1000))==independent,$"{count} files: IPC list and pulse remain independent of stalled transfer");
   var sameSession=engine.Handle(sid,new(){Action="retry",SessionId=ids[0]},cancellation.Token);
   await engine.Tick(cancellation.Token);
   Check(!sameSession.IsCompleted && server.SaveCounts[ids[0]]==1,$"{count} files: overlapping ticks and same-session IPC serialize behind uncertain upload");
   await engine.LeaseTick(cancellation.Token);
   Check(ids.All(id=>server.Leases.ContainsKey(id)),$"{count} files: independent lease renews every file including stalled upload");
   Check(server.PendingCounts[ids[0]]==1,$"{count} files: heartbeat exposes bounded persisted queue telemetry during stalled upload");
   var deadline=DateTimeOffset.UtcNow.AddSeconds(6);
   while(server.Saved.Count<count-1 && DateTimeOffset.UtcNow<deadline) await Task.Delay(30);
   Check(server.Saved.Count==count-1,$"{count} files: every healthy file receives ACK while first transfer stalls");
   Check(server.MaxTransfers<=4,$"{count} files: concurrent transfer cap is four");
   var changedDuringUpload=EngineChecks.Document("save after uncertain request began");
   File.WriteAllBytes(Path.Combine(root,"work",sid,ids[0],"test.docx"),changedDuringUpload);
   server.Release.TrySetResult();await tick;await sameSession;
   var afterAck=DurableJson.Read<SessionState>(Path.Combine(root,"state",ids[0]+".json"))!;
   Check(afterAck.Pending?.Hash==Convert.ToHexStringLower(SHA256.HashData(changedDuringUpload)) && afterAck.AckHash!=afterAck.Pending.Hash,$"{count} files: an older upload ACK cannot hide a newer Word save");
   }
   foreach(var id in ids) {
    File.WriteAllBytes(Path.Combine(root,"work",sid,id,"test.docx"),EngineChecks.Document("offline update "+id));
    var statePath=Path.Combine(root,"state",id+".json");var state=DurableJson.Read<SessionState>(statePath)!;state.RetryAt=DateTimeOffset.MinValue;DurableJson.Write(statePath,state);
   }
   server.Offline=true;
   using(var offline=new SyncEngine(root,config,new HttpClient(server,false))) {
    offline.Initialize();await offline.Tick(default);
    Check(ids.All(id=>{var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;return state.Pending!=null && File.Exists(state.Pending.Path) && state.Status=="offline";}),$"{count} files: offline restart preserves a durable uncertain upload for every session");
    var uncertainKeys=ids.ToDictionary(id=>id,id=>DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!.Pending!.Key);
    foreach(var id in ids)File.WriteAllBytes(Path.Combine(root,"work",sid,id,"test.docx"),EngineChecks.Document("newest offline update "+id));
    await offline.Tick(default);
    Check(ids.All(id=>{var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;return state.Pending!=null && state.NextPending!=null && Directory.EnumerateFiles(Path.Combine(root,"snapshots",id),"*.docx").Count()==2;}),$"{count} files: offline changes coalesce into exactly two durable snapshots");
    foreach(var id in ids)File.WriteAllBytes(Path.Combine(root,"work",sid,id,"test.docx"),EngineChecks.Document("coalesced latest update "+id));
    await offline.Tick(default);
    Check(ids.All(id=>{var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;return state.Pending?.Key==uncertainKeys[id] && state.NextPending?.Hash==Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(state.WorkingPath))) && Directory.EnumerateFiles(Path.Combine(root,"snapshots",id),"*.docx").Count()==2;}),$"{count} files: coalescing preserves uncertain idempotency key and latest local bytes");
   }
   server.Offline=false;
   foreach(var id in ids) {var statePath=Path.Combine(root,"state",id+".json");var state=DurableJson.Read<SessionState>(statePath)!;state.RetryAt=DateTimeOffset.MinValue;DurableJson.Write(statePath,state);}
   using(var restored=new SyncEngine(root,config,new HttpClient(server,false))) {
    restored.Initialize();await restored.Tick(default);
    foreach(var id in ids)await restored.Handle(sid,new(){Action="retry",SessionId=id},default);
    await restored.Tick(default);
    Check(ids.All(id=>{var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;return state.Pending is null && state.NextPending is null && state.AckHash==Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(state.WorkingPath)));}),$"{count} files: reconnect drains both snapshots with exact latest-byte ACK");
   }
  } finally { server.Release.TrySetResult();if(Directory.Exists(root))Directory.Delete(root,true); }
 }
 private sealed class MultiServer : HttpMessageHandler {
  internal string Stalled="";
  internal bool Offline;
  internal TaskCompletionSource Started=new(TaskCreationOptions.RunContinuationsAsynchronously),Release=new(TaskCreationOptions.RunContinuationsAsynchronously);
  internal ConcurrentDictionary<string,bool> Saved=[],Leases=[];internal ConcurrentDictionary<string,int> SaveCounts=[],PendingCounts=[];internal int MaxTransfers;private int transfers;
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct) {
   var parts=request.RequestUri!.AbsolutePath.Split('/');var id=parts[^2];var operation=parts[^1];
   object Session(string hash)=>new{session_id=id,revision=2,sha256=hash,expires_at=DateTimeOffset.UtcNow.AddHours(1),lease_expires_at=DateTimeOffset.UtcNow.AddSeconds(90)};
   if(operation=="save") {
    SaveCounts.AddOrUpdate(id,1,(_,count)=>count+1);
    var active=Interlocked.Increment(ref transfers);int prior;
    do{prior=MaxTransfers;}while(active>prior && Interlocked.CompareExchange(ref MaxTransfers,active,prior)!=prior);
    try {
     if(id==Stalled){Started.TrySetResult();await Release.Task.WaitAsync(ct);}else await Task.Delay(70,ct);
     if(Offline)return new(HttpStatusCode.ServiceUnavailable);
     var bytes=await request.Content!.ReadAsByteArrayAsync(ct);Saved[id]=true;
     return new(HttpStatusCode.OK){Content=JsonContent.Create(Session(Convert.ToHexStringLower(SHA256.HashData(bytes))),options:Wire.Json)};
    }finally{Interlocked.Decrement(ref transfers);}
   }
   if(operation=="heartbeat") {
    Leases[id]=true;var body=await request.Content!.ReadFromJsonAsync<System.Text.Json.JsonElement>(Wire.Json,ct);
    PendingCounts[id]=body.GetProperty("pending_uploads").GetInt32();
    if(PendingCounts[id] is <0 or >2)throw new Exception("queue telemetry exceeds two durable snapshots");
   }
   return new(HttpStatusCode.OK){Content=JsonContent.Create(Session(""),options:Wire.Json)};
  }
 }
}
