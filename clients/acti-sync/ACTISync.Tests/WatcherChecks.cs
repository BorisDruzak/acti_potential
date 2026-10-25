using ACTISync.Core;
using ACTISync.Service;
using System.Collections;
using System.Net;
using System.Net.Http.Json;
using System.Reflection;
using System.Security.Cryptography;
using System.Security.Principal;

internal static class WatcherChecks {
 private static void Check(bool condition,string message) {if(!condition)throw new Exception(message);Console.WriteLine("PASS "+message);}
 private static FileSystemWatcher[] Watchers(SyncEngine engine) {
  var value=typeof(SyncEngine).GetField("watchers",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(engine)!;
  return (value is IDictionary map ? map.Values : (IEnumerable)value).Cast<FileSystemWatcher>().ToArray();
 }
 private static Dictionary<string,SessionState> Sessions(SyncEngine engine)=>(Dictionary<string,SessionState>)typeof(SyncEngine).GetField("sessions",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(engine)!;
 private static async Task Changed(SessionState state) {
  for(var i=0;i<60 && !state.Dirty;i++)await Task.Delay(50);
  Check(state.Dirty,"active session still observes current working-file changes");
 }
 internal static async Task Run() {await CompletedHistory();await SessionLifetime();}
 private static async Task CompletedHistory() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-watch-history-"+Guid.NewGuid().ToString("N"));
  var sid=WindowsIdentity.GetCurrent().User!.Value;
  try {
   Directory.CreateDirectory(Path.Combine(root,"state"));
   var records=new List<SessionState>();
   for(var i=0;i<33;i++) {
    var id=Guid.NewGuid().ToString("D");var work=Path.Combine(root,"work",sid,id);Directory.CreateDirectory(work);
    var state=new SessionState{SessionId=id,Sid=sid,ServerOrigin="https://example.com/",WorkingPath=Path.Combine(work,"test.docx"),Finished=i<32,Status=i<32?"finished":"editing"};
    File.WriteAllBytes(state.WorkingPath,EngineChecks.Document("preserved original"));
    DurableJson.Write(Path.Combine(root,"state",id+".json"),state);records.Add(state);
   }
   using var engine=new SyncEngine(root,new ClientConfig(new Uri("https://example.com/")),new HttpClient()){NativeDispatchPaused=()=>false};
   engine.Initialize();
   Check(Watchers(engine).Length==1,"completed session history acquires no native watchers after restart");
   Check((await engine.Handle(sid,new(),default)).Sessions.Count==1,"completed history stays archived while active session recovers");
   Check(records.All(state=>File.Exists(state.WorkingPath) && File.Exists(Path.Combine(root,"state",state.SessionId+".json"))),"watcher recovery preserves completed state and working originals");
   File.WriteAllBytes(records[^1].WorkingPath,EngineChecks.Document("changed active original"));
   await Changed(Sessions(engine)[records[^1].SessionId]);
  } finally {if(Directory.Exists(root))Directory.Delete(root,true);}
 }
 private static async Task SessionLifetime() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-watch-lifetime-"+Guid.NewGuid().ToString("N"));
  var sid=WindowsIdentity.GetCurrent().User!.Value;using var server=new Server();
  try {
   using var engine=new SyncEngine(root,new ClientConfig(new Uri("https://example.com/")),new HttpClient(server,false)){NativeDispatchPaused=()=>false};
   engine.Initialize();
   var first=(await engine.Handle(sid,new(){Action="launch",Ticket=new string('a',43)},default)).Session!;
   var firstWatcher=Watchers(engine).Single();
   var owner=Path.Combine(Path.GetDirectoryName(first.WorkingPath)!,"~$test.docx");File.WriteAllText(owner,"owner");
   Check(!(await engine.Handle(sid,new(){Action="finish",SessionId=first.SessionId},default)).Ok && firstWatcher.EnableRaisingEvents,"refused finish keeps active watcher and local copy");
   File.Delete(owner);
   var replacement=(await engine.Handle(sid,new(){Action="launch",Ticket=new string('b',43)},default)).Session!;
   Check(replacement.WorkingPath==first.WorkingPath && replacement.SessionId!=first.SessionId,"session replacement retains the existing working original");
   Check(Watchers(engine).Length==1 && !firstWatcher.EnableRaisingEvents,"session replacement disposes its predecessor's native watcher");
   var currentWatcher=Watchers(engine).Single();
   File.WriteAllBytes(replacement.WorkingPath,server.Bytes);await Changed(Sessions(engine)[replacement.SessionId]);
   Check((await engine.Handle(sid,new(){Action="finish",SessionId=replacement.SessionId},default)).Ok,"unchanged replacement can finish normally");
   Check(Watchers(engine).Length==0 && !currentWatcher.EnableRaisingEvents,"successful finish releases the final native watcher");
   Check(File.Exists(first.WorkingPath) && new[]{first.SessionId,replacement.SessionId}.All(id=>File.Exists(Path.Combine(root,"state",id+".json"))),"finishing and replacing watchers preserve both durable session records and original");
  } finally {if(Directory.Exists(root))Directory.Delete(root,true);}
 }
 private sealed class Server:HttpMessageHandler {
  internal byte[] Bytes=EngineChecks.Document("server original");
  private string session="";
  protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct) {
   if(request.RequestUri!.AbsolutePath.EndsWith("/download"))return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK){Content=new ByteArrayContent(Bytes)});
   if(request.RequestUri.AbsolutePath.EndsWith("/exchange"))session=Guid.NewGuid().ToString("D");
   return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK){Content=JsonContent.Create(new{session_id=session,token="test-only-scoped-token",file_id="stable-file",filename="test.docx",revision=1,sha256=Convert.ToHexStringLower(SHA256.HashData(Bytes)),expires_at=DateTimeOffset.UtcNow.AddMinutes(15),lease_expires_at=DateTimeOffset.UtcNow.AddSeconds(90),agent=new{connection_id=Guid.NewGuid().ToString("D"),token="test-only-agent-token",expires_at=DateTimeOffset.UtcNow.AddMinutes(15)}},options:Wire.Json)});
  }
 }
}
