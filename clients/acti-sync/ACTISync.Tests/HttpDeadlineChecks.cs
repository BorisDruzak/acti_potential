using ACTISync.Core;
using ACTISync.Service;
using System.Net;
using System.Security.Principal;

internal static class HttpDeadlineChecks {
 internal static async Task Run() {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-deadline-"+Guid.NewGuid().ToString("N"));var config=new ClientConfig(new Uri("https://example.com/"));var sid=WindowsIdentity.GetCurrent().User!.Value;var id=Guid.NewGuid().ToString();
  using var server=new BodyServer();
  try {
   using(var setup=new SyncEngine(root,config,new HttpClient(server,false)))setup.Initialize();
   var work=Path.Combine(root,"work",sid,id);Directory.CreateDirectory(work);Directory.CreateDirectory(Path.Combine(root,"snapshots",id));
   var file=Path.Combine(work,"test.docx");File.WriteAllBytes(file,EngineChecks.Document("body must not hang"));File.WriteAllText(Path.Combine(work,"~$test.docx"),"owner");
   var statePath=Path.Combine(root,"state",id+".json");DurableJson.Write(statePath,new SessionState{SessionId=id,Sid=sid,WorkingPath=file,FileName="test.docx",ServerOrigin=config.Origin.AbsoluteUri,ProtectedToken=Security.Protect("token"),ExpiresAt=DateTimeOffset.UtcNow.AddHours(1),Revision=1,AckHash=new string('0',64),Opened=true,DocumentOpen=true});
   using var engine=new SyncEngine(root,config,new HttpClient(server,false)){RequestTimeout=TimeSpan.FromMilliseconds(150)};engine.Initialize();
   await engine.Tick(default).WaitAsync(TimeSpan.FromSeconds(4));
   var state=DurableJson.Read<SessionState>(statePath)!;
   if(state.Status!="offline" || state.ErrorCode!="network" || state.Pending is null || !File.Exists(state.Pending.Path) || state.LastAck!=null)throw new Exception("stalled response body must time out without dropping or acknowledging pending bytes");
   Console.WriteLine("PASS bounded HTTP deadline includes stalled response body and preserves uncertain upload");
  }finally{if(Directory.Exists(root))Directory.Delete(root,true);}
 }
 private sealed class BodyServer : HttpMessageHandler {
  protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct)=>Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK){Content=new StalledBody()});
 }
 private sealed class StalledBody : HttpContent {
  protected override bool TryComputeLength(out long length){length=0;return false;}
  protected override Task SerializeToStreamAsync(Stream stream,TransportContext? context)=>Task.Delay(Timeout.InfiniteTimeSpan);
  protected override Task SerializeToStreamAsync(Stream stream,TransportContext? context,CancellationToken ct)=>Task.Delay(Timeout.InfiniteTimeSpan,ct);
 }
}
