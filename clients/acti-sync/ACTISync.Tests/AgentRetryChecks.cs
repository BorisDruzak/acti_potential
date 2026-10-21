using ACTISync.Core;
using ACTISync.Service;
using System.Net;
using System.Net.Http.Json;
using System.Security.Principal;
using System.Text.Json;

internal static class AgentRetryChecks {
 internal static async Task Run(){await Denied(HttpStatusCode.Unauthorized);await Denied(HttpStatusCode.Forbidden);await Transient();await RotatedWhileDenied();await CommandsDenied();await UnwritableState();}
 private static void Check(bool ok,string message){if(!ok)throw new Exception(message);Console.WriteLine("PASS "+message);}
 private sealed class Fixture:IDisposable {
  internal readonly string Root=Path.Combine(Path.GetTempPath(),"ACTISync-relay-retry-"+Guid.NewGuid().ToString("N"));
  internal readonly string Sid=WindowsIdentity.GetCurrent().User!.Value;
  internal readonly ClientConfig Config=new(new Uri("https://example.com/"));
  internal DateTimeOffset Now=DateTimeOffset.UtcNow;
  internal readonly Server Api=new();
  internal string StatePath=>Path.Combine(Root,"agents",Api.Id+".json");
  internal SyncEngine Engine(){var e=new SyncEngine(Root,Config,new HttpClient(Api,false)){Clock=()=>Now,NativeDispatchPaused=()=>false};e.Initialize();return e;}
  internal async Task Seed(){using var e=Engine();Check((await Connect(e)).Ok,"isolated relay credentials issued");var s=DurableJson.Read<AgentState>(StatePath)!;s.Results.Add(new(){Id=Guid.NewGuid().ToString(),Ok=true});var c=new AgentCommand{Id=Guid.NewGuid().ToString(),SessionId=Guid.NewGuid().ToString(),Action="reveal",ExpiresAt=Now.AddMinutes(10)};s.Pending[c.Id]=c;DurableJson.Write(StatePath,s);}
  internal Task<IpcResponse> Connect(SyncEngine e)=>e.Handle(Sid,new(){Action="connect",Ticket=new string('a',43)},default);
  internal Task<IpcResponse> Pulse(SyncEngine e)=>e.Handle(Sid,new(){Action="pulse"},default);
  public void Dispose(){Api.Release.TrySetResult();Api.Dispose();if(Directory.GetParent(Root)?.FullName!=Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar))throw new Exception("Unexpected fixture path");if(Directory.Exists(Root))Directory.Delete(Root,true);}
 }
 private static async Task Denied(HttpStatusCode code){
  using var f=new Fixture();f.Api.Failure=code;await f.Seed();
  using(var e=f.Engine()){
   for(var i=0;i<20;i++){f.Now=f.Now.AddSeconds(2);await f.Pulse(e);await e.AgentTick(default);}
   Check(f.Api.Renews==1,$"{code}: a rejected credential is attempted only once");
   Check((await f.Pulse(e)).Commands.Count==0 && !(await e.Handle(f.Sid,new(){Action="presence"},default)).AgentOnline,"known rejected credentials cannot advertise online or dispatch queued native commands");
   var state=DurableJson.Read<AgentState>(f.StatePath)!;Check(state.Results.Count==1 && state.Pending.Count==1,"authorization pause preserves pending commands and durable receipts");
  }
  using(var restarted=f.Engine()){
   await f.Pulse(restarted);await restarted.AgentTick(default);Check(f.Api.Renews==1,"service restart retains the authorization pause");
   f.Api.Failure=HttpStatusCode.OK;f.Api.Fresh=true;Check((await f.Connect(restarted)).Ok,"fresh browser ticket reconnects without dropping receipts");
   await f.Pulse(restarted);await restarted.AgentTick(default);Check(f.Api.Heartbeats==1 && f.Api.DeliveredResults==1,"fresh scoped credentials resume relay and deliver the retained receipt");
  }
 }
 private static async Task Transient(){
  using var f=new Fixture();f.Api.Failure=HttpStatusCode.ServiceUnavailable;await f.Seed();using var e=f.Engine();
  await f.Pulse(e);await e.AgentTick(default);for(var i=0;i<5;i++)await e.AgentTick(default);Check(f.Api.Renews==1,"transient relay failure is not retried on every service tick");
  f.Now=f.Now.AddSeconds(2);await f.Pulse(e);await e.AgentTick(default);Check(f.Api.Renews==2,"relay retry starts after the first bounded delay");
  f.Now=f.Now.AddSeconds(2);await f.Pulse(e);await e.AgentTick(default);Check(f.Api.Renews==2,"repeated transient failures increase the delay");
  f.Now=f.Now.AddSeconds(2);f.Api.Failure=HttpStatusCode.OK;await f.Pulse(e);await e.AgentTick(default);Check(f.Api.Heartbeats==1,"transient recovery resumes automatically without a browser ticket");
 }
 private static async Task RotatedWhileDenied(){
  using var f=new Fixture();await f.Seed();using var e=f.Engine();f.Api.Hold=true;await f.Pulse(e);var old=e.AgentTick(default);await f.Api.Started.Task.WaitAsync(TimeSpan.FromSeconds(3));
  f.Api.Fresh=true;await f.Connect(e);f.Api.Release.TrySetResult();await old;await e.AgentTick(default);
  Check(f.Api.Heartbeats==1,"a late rejection of an old token cannot pause newly rotated credentials");
 }
 private static async Task CommandsDenied(){
  using var f=new Fixture();await f.Seed();
  var state=DurableJson.Read<AgentState>(f.StatePath)!;var command=state.Pending.Values.Single();
  var started=new AgentCommand{Id=Guid.NewGuid().ToString(),SessionId=command.SessionId,Action="reveal",ExpiresAt=f.Now.AddMinutes(10)};
  state.Pending[started.Id]=started;state.Started.Add(started.Id);DurableJson.Write(f.StatePath,state);
  var working=Path.Combine(f.Root,"work",f.Sid,command.SessionId,"fixture.docx");Directory.CreateDirectory(Path.GetDirectoryName(working)!);
  DurableJson.Write(Path.Combine(f.Root,"state",command.SessionId+".json"),new SessionState{SessionId=command.SessionId,Sid=f.Sid,ServerOrigin=f.Config.Origin.AbsoluteUri,ConnectionId=f.Api.Id,WorkingPath=working,FileName="fixture.docx"});
  // Initialize normally treats a started reveal as uncertain and completes it.
  // Start it after initialization to test a result arriving during auth loss.
  state.Started.Clear();DurableJson.Write(f.StatePath,state);
  using var e=f.Engine();Check((await e.Handle(f.Sid,new(){Action="command_start",CommandId=started.Id,SessionId=started.SessionId},default)).Ok,"isolated native command starts while authorized");
  f.Api.Failure=HttpStatusCode.Unauthorized;await f.Pulse(e);await e.AgentTick(default);
  Check(!(await e.Handle(f.Sid,new(){Action="command_start",CommandId=command.Id,SessionId=command.SessionId},default)).Ok,"a command already handed to the user cannot start after authorization is rejected");
  Check((await e.Handle(f.Sid,new(){Action="command_result",CommandId=started.Id,SessionId=started.SessionId,Ok=true},default)).Ok,"an already started native action can persist its result during authorization pause");
  Check((await e.Handle(f.Sid,new(){Action="command_result",CommandId=started.Id,SessionId=started.SessionId,Ok=true},default)).Ok,"completed receipts remain idempotently available during authorization pause");
 }
 private static async Task UnwritableState(){
  using var f=new Fixture();await f.Seed();using(var e=f.Engine()){
   var backup=f.StatePath+".backup";File.Move(f.StatePath,backup);Directory.CreateDirectory(f.StatePath);
   try{f.Api.Failure=HttpStatusCode.Unauthorized;await f.Pulse(e);await e.AgentTick(default);await e.AgentTick(default);Check(f.Api.Renews==1,"unwritable retry state cannot kill the relay or bypass the in-memory authorization pause");}
   finally{Directory.Delete(f.StatePath);File.Move(backup,f.StatePath);}
   await e.AgentTick(default);Check(DurableJson.Read<AgentState>(f.StatePath)!.AuthorizationRequired,"authorization pause is made durable after storage recovers");
  }
  using var restarted=f.Engine();await f.Pulse(restarted);await restarted.AgentTick(default);Check(f.Api.Renews==1,"recovered retry state remains paused after restart");
 }
 private sealed class Server:HttpMessageHandler {
  internal string Id=Guid.NewGuid().ToString();internal HttpStatusCode Failure=HttpStatusCode.OK;internal bool Fresh,Hold;internal int Renews,Heartbeats,DeliveredResults;
  internal TaskCompletionSource Started=new(TaskCreationOptions.RunContinuationsAsynchronously),Release=new(TaskCreationOptions.RunContinuationsAsynchronously);
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct){
   object Credentials()=>new{connection_id=Id,token=Fresh?"fresh-credential":"old-credential",expires_at=DateTimeOffset.UtcNow.AddMinutes(Fresh?15:1)};
   HttpResponseMessage Json(object body)=>new(HttpStatusCode.OK){Content=JsonContent.Create(body,options:Wire.Json)};
   if(request.RequestUri!.AbsolutePath.EndsWith("agent/exchange"))return Json(Credentials());
   if(request.RequestUri.AbsolutePath.EndsWith("/renew")){Renews++;if(Hold){Hold=false;Started.TrySetResult();await Release.Task.WaitAsync(ct);return new(HttpStatusCode.Unauthorized);}if(Failure!=HttpStatusCode.OK)return new(Failure);Fresh=true;return Json(Credentials());}
   Heartbeats++;var body=await request.Content!.ReadFromJsonAsync<JsonElement>(Wire.Json,ct);DeliveredResults+=body.GetProperty("command_results").GetArrayLength();return Json(new{commands=Array.Empty<object>()});
  }
 }
}
