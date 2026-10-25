using ACTISync.Core;
using ACTISync.Service;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;

internal static class LeaseResumeChecks {
 internal static async Task Run() {
  foreach(var response in new[]{HttpStatusCode.OK,HttpStatusCode.Conflict,HttpStatusCode.Unauthorized,HttpStatusCode.NotFound,HttpStatusCode.ServiceUnavailable})await Restart(response);
  await Restart(HttpStatusCode.OK,lostSaveAck:true);
  await Restart(HttpStatusCode.OK,wrongProof:true);
 }
 private static async Task Restart(HttpStatusCode response,bool lostSaveAck=false,bool wrongProof=false) {
  var root=Path.Combine(Path.GetTempPath(),"ACTISync-lease-resume-"+Guid.NewGuid().ToString("N"));
  var config=new ClientConfig(new Uri("https://example.com/"));var sid=WindowsIdentity.GetCurrent().User!.Value;
  var id=Guid.NewGuid().ToString();var ack=new string('a',64);
  using var server=new ResumeServer(id,ack,response){LostSaveAck=lostSaveAck,WrongProof=wrongProof};
  try {
   using(var first=new SyncEngine(root,config,new HttpClient(server,false)))first.Initialize();
   var work=Path.Combine(root,"work",sid,id);Directory.CreateDirectory(work);Directory.CreateDirectory(Path.Combine(root,"snapshots",id));
   var path=Path.Combine(work,"pending.docx");var bytes=EngineChecks.Document("saved while service unavailable");File.WriteAllBytes(path,bytes);File.WriteAllText(Path.Combine(work,"~$pending.docx"),"owner");
   var statePath=Path.Combine(root,"state",id+".json");
   DurableJson.Write(statePath,new SessionState{SessionId=id,Sid=sid,ServerOrigin=config.Origin.AbsoluteUri,ProtectedToken=Security.Protect("scoped-token"),WorkingPath=path,FileName="pending.docx",FileId=id,Revision=1,AckHash=ack,Opened=true,DocumentOpen=true,ExpiresAt=DateTimeOffset.UtcNow.AddMinutes(10),LeaseExpiresAt=DateTimeOffset.UtcNow.AddSeconds(-10),Status="conflict"});
   if(lostSaveAck){var state=DurableJson.Read<SessionState>(statePath)!;var snapshot=await SafeFiles.SnapshotAsync(path,Path.Combine(root,"snapshots",id),default) ?? throw new Exception();snapshot.ExpectedRevision=1;snapshot.Key="uncertain-save";state.Pending=snapshot;DurableJson.Write(statePath,state);server.CommittedHash=snapshot.Hash;}
   using(var restored=new SyncEngine(root,config,new HttpClient(server,false))){restored.Initialize();await restored.Tick(default);}
   var result=DurableJson.Read<SessionState>(statePath)!;
   if(response==HttpStatusCode.OK && !wrongProof) {
    Check(server.Resumes==1 && server.Saves==1 && result.Pending is null && result.AckHash==Convert.ToHexStringLower(SHA256.HashData(bytes)),"expired unchanged lease resumes and uploads pending bytes after restart");
    Check(Security.Unprotect(result.ProtectedToken)=="scoped-token" && result.Status=="saved","lease resume keeps the scoped bearer and authoritative save ACK");
   }else {
    Check(server.Resumes==1 && server.Saves==0 && result.Pending!=null && File.ReadAllBytes(result.Pending.Path).SequenceEqual(bytes),$"resume {response} never uploads or discards pending bytes");
    Check(result.Revision==1 && result.AckHash==ack,$"resume {response} never rebases pending content onto a new server revision");
   }
   Check(File.ReadAllBytes(path).SequenceEqual(bytes),"lease recovery never rewrites the open Word document");
  }finally{if(Directory.GetParent(root)?.FullName!=Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar))throw new Exception("Unexpected test root");if(Directory.Exists(root))Directory.Delete(root,true);}
 }
 private static void Check(bool ok,string message){if(!ok)throw new Exception(message);Console.WriteLine("PASS "+message);}
 private sealed class ResumeServer(string id,string ack,HttpStatusCode response):HttpMessageHandler {
  internal int Resumes,Saves;
  internal bool LostSaveAck,WrongProof;internal string CommittedHash="";
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct) {
   var op=request.RequestUri!.Segments.Last();
   if(op=="resume") {
    Resumes++;var body=await request.Content!.ReadFromJsonAsync<JsonElement>(Wire.Json,ct);
    Check(body.GetProperty("revision").GetInt32()==1 && body.GetProperty("sha256").GetString()==ack,"resume compares the prior ACK rather than the newer pending snapshot");
    if(response!=HttpStatusCode.OK)return new(response);
    if(WrongProof)return Reply(99,ack);
    if(LostSaveAck){var pending=body.GetProperty("pending");Check(pending.GetProperty("key").GetString()=="uncertain-save" && pending.GetProperty("sha256").GetString()==CommittedHash && pending.GetProperty("revision").GetInt32()==1,"lost save ACK resumes only through the exact persisted pending receipt proof");return Reply(2,CommittedHash);}
    return Reply(1,ack);
   }
   if(op=="save") {Saves++;Check(Resumes==1,"expired lease is reacquired before attempting a write");if(LostSaveAck)Check(request.Headers.GetValues("Idempotency-Key").Single()=="uncertain-save" && request.Headers.IfMatch.Single().Tag=="\"1\"","lost save ACK replays the same key and expected revision without rebasing");var bytes=await request.Content!.ReadAsByteArrayAsync(ct);return Reply(2,Convert.ToHexStringLower(SHA256.HashData(bytes)));}
   return Reply(1,ack);
  }
  private HttpResponseMessage Reply(int revision,string sha)=>new(HttpStatusCode.OK){Content=JsonContent.Create(new{session_id=id,file_id=id,revision,sha256=sha,expires_at=DateTimeOffset.UtcNow.AddMinutes(10),lease_expires_at=DateTimeOffset.UtcNow.AddSeconds(90)},options:Wire.Json)};
 }
}
