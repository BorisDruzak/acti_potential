using ACTISync.Core;
using ACTISync.Service;
using System.IO.Compression;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Security.Principal;

internal static class EngineChecks {
 private static void Check(bool condition,string message) { if(!condition) throw new Exception(message); Console.WriteLine("PASS "+message); }
 internal static byte[] Document(string text) { using var memory=new MemoryStream(); using(var zip=new ZipArchive(memory,ZipArchiveMode.Create,true)) { using(var writer=new StreamWriter(zip.CreateEntry("[Content_Types].xml").Open())) writer.Write("<Types />"); using(var writer=new StreamWriter(zip.CreateEntry("word/document.xml").Open())) writer.Write(text); } return memory.ToArray(); }
 internal static async Task Run() {
  await WatcherChecks.Run();
  await LegacyDoc();
  await ClosedWord();
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-engine-"+Guid.NewGuid().ToString("N"));
  var server=new Server(); var config=new ClientConfig(new Uri("https://example.com/")); var sid=WindowsIdentity.GetCurrent().User!.Value;
  try {
   string id,path;
   using(var engine=new SyncEngine(root,config,new HttpClient(server,false))) {
    engine.Initialize(); var launch=await engine.Handle(sid,new(){Action="launch",Ticket=new string('a',43)},default);
    Check(launch.Ok && launch.Session?.Status=="opening","exchange downloads verified DOCX without claiming Word opened");
    id=launch.Session!.SessionId; path=launch.Session.WorkingPath;
    Check(!File.ReadAllText(Path.Combine(root,"state",id+".json")).Contains(Server.Token,StringComparison.Ordinal),"credential persisted only through DPAPI");
    var crossUser=await engine.Handle("S-1-5-21-1-2-3-1234",new(){Action="opened",SessionId=id},default); Check(!crossUser.Ok,"cross-user session IPC rejected");
    var unconfirmed=await engine.Handle(sid,new(){Action="opened",SessionId=id},default); Check(!unconfirmed.Ok,"process launch alone cannot confirm Word document open");
    File.WriteAllText(Path.Combine(Path.GetDirectoryName(path)!,"~$test.docx"),"owner");
    await engine.Handle(sid,new(){Action="opened",SessionId=id},default);
    using(var locked=new FileStream(path,FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite|FileShare.Delete)) {
     locked.Lock(0,1);
     try {
      await engine.Tick(default);
      var waiting=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;
      Check(waiting.Status=="editing" && waiting.Failures==0 && server.Heartbeats==2,"Word byte-range lock preserves editing and renews server lease");
     } finally { locked.Unlock(0,1); }
    }
    File.WriteAllBytes(path,Document("local offline change"));
    server.SaveStatus=HttpStatusCode.ServiceUnavailable; await engine.Tick(default);
    var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;
    Check(state.Status=="offline" && state.Pending!=null && File.Exists(state.Pending.Path) && state.LastAck==null,"network failure keeps durable pending and never acknowledges");
    Check(server.SaveRequests==1,"exactly one failed upload attempt");
   }
   await Task.Delay(2100);
   server.SaveStatus=HttpStatusCode.OK;
   using(var recovered=new SyncEngine(root,config,new HttpClient(server,false))) {
    recovered.Initialize(); await recovered.Tick(default);
    var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;
    Check(state.Pending==null && state.LastAck!=null && state.Status=="saved","service restart recovers pending and accepts explicit commit ACK");
    Check(server.Keys.Count==2 && server.Keys[0]==server.Keys[1],"uncertain upload retry retains same idempotency key");
    Check(state.AckHash==Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(path))),"ACK binds actual local bytes");
    await recovered.Tick(default); Check(server.SaveRequests==2,"unchanged periodic hash does not create another revision");
    File.WriteAllBytes(path,Document("not yet queued because storage quota is exhausted"));
    using(var quota=new FileStream(Path.Combine(root,"snapshots",id,"quota-test"),FileMode.CreateNew,FileAccess.Write,FileShare.ReadWrite,4096,FileOptions.DeleteOnClose)) {
     if(!QuotaFixture.DeviceIoControl(quota.SafeFileHandle,0x000900C4,IntPtr.Zero,0,IntPtr.Zero,0,out _,IntPtr.Zero)) throw new Exception("sparse quota fixture failed");
     quota.SetLength(1024L*1024*1024+1);
     var beforeFinish=server.Requests; var refused=await recovered.Handle(sid,new(){Action="finish",SessionId=id},default);
     Check(!refused.Ok && server.Requests==beforeFinish,"finish refuses changed DOCX when quota prevents durable snapshot");
     var visible=await recovered.Handle(sid,new(),default); Check(visible.Sessions.Any(s=>s.SessionId==id) && File.Exists(path),"refused finish keeps unsent document visible and recoverable");
    }
    await recovered.Handle(sid,new(){Action="retry",SessionId=id},default);
    File.WriteAllBytes(path,Document("conflicting local change")); server.SaveStatus=HttpStatusCode.Conflict;
    await recovered.Tick(default); state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;
    Check(state.Status=="conflict" && state.Pending!=null && File.Exists(path),"CAS conflict preserves local file and pending snapshot");
    var count=server.SaveRequests; await recovered.Tick(default); Check(server.SaveRequests==count,"conflict never silently retries overwrite");
    File.Delete(Path.Combine(Path.GetDirectoryName(path)!,"~$test.docx"));
    await recovered.Tick(default); state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;
    Check(state.DocumentOpen==false && state.Status=="conflict" && state.Pending!=null && server.LastDocumentOpen==false,"observed Word close reports independently of conflict and pending upload ACK");
    var finish=await recovered.Handle(sid,new(){Action="finish",SessionId=id},default); Check(!finish.Ok,"pending edit cannot be accidentally finished");
   }
   var requests=server.Requests;
   using(var moved=new SyncEngine(root,new ClientConfig(new Uri("https://other.example.com/")),new HttpClient(server,false))) { moved.Initialize(); await moved.Tick(default); var list=await moved.Handle(sid,new(),default); Check(list.Sessions.Single().Status=="reauth" && server.Requests==requests,"changed installed origin sends no recovered credential or document"); }
   Console.WriteLine("ALL SERVICE RECOVERY CHECKS PASSED");
  } finally { Directory.Delete(root,true); server.Dispose(); }
 }
 private static async Task LegacyDoc() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-doc-"+Guid.NewGuid().ToString("N"));
  using var server=new Server(true); var sid=WindowsIdentity.GetCurrent().User!.Value;
  var config=new ClientConfig(new Uri("https://example.com/")); string id,path;
  try {
   using(var engine=new SyncEngine(root,config,new HttpClient(server,false))) {
    engine.Initialize(); var launched=await engine.Handle(sid,new(){Action="launch",Ticket=new string('d',43)},default);
    Check(launched.Ok && launched.Session?.Status=="opening","legacy DOC is downloaded as an immutable conversion input");
    id=launched.Session!.SessionId; path=launched.Session.WorkingPath;
    Check(path.EndsWith("test.docx") && !File.Exists(path),"DOC bytes never masquerade as DOCX or enter snapshot queue");
    var source=Path.Combine(Path.GetDirectoryName(path)!,"original.doc");
    Check(File.ReadAllBytes(source).SequenceEqual(server.Initial),"private DOC source bytes match server checksum");
    await engine.Tick(default); Check(server.SaveRequests==0,"waiting for Word conversion never uploads DOC bytes");
   }
   using(var restarted=new SyncEngine(root,config,new HttpClient(server,false))) {
    restarted.Initialize();
    Check((await restarted.Handle(sid,new(),default)).Sessions.Single().SessionId==id,"pending DOC conversion survives service restart");
    File.WriteAllBytes(path,Document("Word conversion output"));
    File.WriteAllText(Path.Combine(Path.GetDirectoryName(path)!,"~$test.docx"),"owner");
    await restarted.Handle(sid,new(){Action="opened",SessionId=id},default);
    await restarted.Tick(default);
    var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;
    Check(server.SaveRequests==1 && state.Revision==2 && state.LastAck!=null,"converted DOCX uses existing CAS and acknowledged durable save path");
    Check(state.SourceFormat=="docx","acknowledged conversion switches future downloads to current DOCX, never the old source");
    Check(File.ReadAllBytes(Path.Combine(Path.GetDirectoryName(path)!,"original.doc")).SequenceEqual(server.Initial),"DOC source is preserved after successful DOCX acknowledgement");
   }
   var recoveredState=DurableJson.Read<SessionState>(Path.Combine(root,"state",id+".json"))!;
   recoveredState.RetryAt=DateTimeOffset.MinValue;DurableJson.Write(Path.Combine(root,"state",id+".json"),recoveredState);File.Delete(path);
   using(var afterAck=new SyncEngine(root,config,new HttpClient(server,false))) {
    afterAck.Initialize();await afterAck.Tick(default);
    Check(SafeFiles.ValidDocx(path) && Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(path)))==recoveredState.AckHash,"missing working copy after ACK downloads the current server DOCX instead of reconverting old DOC");
   }
  } finally { if(Directory.Exists(root)) Directory.Delete(root,true); }
 }
 private static async Task ClosedWord() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-close-"+Guid.NewGuid().ToString("N"));
  using var server=new Server(); var sid=WindowsIdentity.GetCurrent().User!.Value;
  try {
   using var engine=new SyncEngine(root,new ClientConfig(new Uri("https://example.com/")),new HttpClient(server,false));
   engine.Initialize(); var launch=await engine.Handle(sid,new(){Action="launch",Ticket=new string('e',43)},default);
   var view=launch.Session!;var owner=Path.Combine(Path.GetDirectoryName(view.WorkingPath)!,"~$test.docx");
   File.WriteAllText(owner,"owner");await engine.Handle(sid,new(){Action="opened",SessionId=view.SessionId},default);await engine.Tick(default);
   File.Delete(owner);await engine.Tick(default);
   var state=DurableJson.Read<SessionState>(Path.Combine(root,"state",view.SessionId+".json"))!;
   Check(state.Finished && state.DocumentOpen==false && server.Finishes==1 && server.SaveRequests==0,"closing unchanged Word releases lease automatically without a binary revision");
   var again=await engine.Handle(sid,new(){Action="launch",Ticket=new string('f',43)},default);
   File.WriteAllText(owner,"owner");await engine.Handle(sid,new(){Action="opened",SessionId=again.Session!.SessionId},default);
   File.WriteAllBytes(again.Session.WorkingPath,Document("save while disconnected, then close"));server.SaveStatus=HttpStatusCode.ServiceUnavailable;
   await engine.Tick(default);File.Delete(owner);await engine.Tick(default);
   state=DurableJson.Read<SessionState>(Path.Combine(root,"state",view.SessionId+".json"))!;
   Check(!state.Finished && state.Pending!=null && server.Finishes==1,"closing Word with pending bytes retains lease and durable queue");
   server.SaveStatus=HttpStatusCode.OK;await engine.Handle(sid,new(){Action="retry",SessionId=view.SessionId},default);await engine.Tick(default);
   state=DurableJson.Read<SessionState>(Path.Combine(root,"state",view.SessionId+".json"))!;
   Check(state.Finished && state.Pending==null && state.Revision==2 && server.Finishes==2,"closed document finishes only after queued bytes receive server ACK");
  } finally { if(Directory.Exists(root))Directory.Delete(root,true); }
 }
 private sealed class Server(bool legacy=false) : HttpMessageHandler {
  public const string Token="test-scoped-credential-never-plaintext";
  private readonly string id=Guid.NewGuid().ToString(); private readonly byte[] initial=legacy ? [0xd0,0xcf,0x11,0xe0,0xa1,0xb1,0x1a,0xe1,..new byte[504]] : Document("server initial");
  internal byte[] Initial=>initial;
  private byte[]? currentBytes;
  private int revision=1; public int Requests,SaveRequests,Heartbeats,Finishes; public bool? LastDocumentOpen; public List<string> Keys=[]; public HttpStatusCode SaveStatus=HttpStatusCode.OK;
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct) {
   Requests++;
   var operation=request.RequestUri!.Segments.Last();
   object Session(string hash)=>new { agent=new{connection_id=Guid.NewGuid().ToString(),token=Token,expires_at=DateTimeOffset.UtcNow.AddMinutes(15)},session_id=id,token=Token,file_id="old-stable-file-id",filename="test.docx",source_format=legacy && revision==1 ? "doc" : "docx",revision,sha256=hash,expires_at=DateTimeOffset.UtcNow.AddMinutes(15),lease_expires_at=DateTimeOffset.UtcNow.AddSeconds(90),last_saved_at=DateTimeOffset.UtcNow,state="saved" };
   if(operation=="exchange") return new(HttpStatusCode.OK){Content=JsonContent.Create(Session(Convert.ToHexStringLower(SHA256.HashData(initial))),options:Wire.Json)};
   if(request.Headers.Authorization?.Parameter!=Token) throw new Exception("scoped token absent");
   if(operation=="download") return new(HttpStatusCode.OK){Content=new ByteArrayContent(currentBytes ?? initial)};
   if(operation=="finish") Finishes++;
   if(operation=="heartbeat") { Heartbeats++; var body=await request.Content!.ReadFromJsonAsync<System.Text.Json.JsonElement>(Wire.Json,ct); if(body.TryGetProperty("document_open",out var open) && open.ValueKind is System.Text.Json.JsonValueKind.True or System.Text.Json.JsonValueKind.False) LastDocumentOpen=open.GetBoolean(); }
   if(operation=="save") {
    SaveRequests++; Keys.Add(request.Headers.GetValues("Idempotency-Key").Single()); Check(request.Headers.GetValues("If-Match").Single()==$"\"{revision}\"","save carries strong expected revision");
    if(SaveStatus!=HttpStatusCode.OK) return new(SaveStatus);
    var bytes=await request.Content!.ReadAsByteArrayAsync(ct); var hash=Convert.ToHexStringLower(SHA256.HashData(bytes)); Check(request.Headers.GetValues("X-Content-SHA256").Single()==hash,"upload checksum matches snapshot bytes"); currentBytes=bytes; revision++;
    return new(HttpStatusCode.OK){Content=JsonContent.Create(Session(hash),options:Wire.Json)};
   }
   return new(HttpStatusCode.OK){Content=JsonContent.Create(Session(""),options:Wire.Json)};
  }
 }
}
internal static class QuotaFixture {
 [System.Runtime.InteropServices.DllImport("kernel32.dll",SetLastError=true)]
 [return:System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)]
 internal static extern bool DeviceIoControl(Microsoft.Win32.SafeHandles.SafeFileHandle handle,uint code,IntPtr input,uint inputSize,IntPtr output,uint outputSize,out uint returned,IntPtr overlapped);
}
