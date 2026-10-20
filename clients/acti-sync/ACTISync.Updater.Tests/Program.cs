using ACTISync.Updater;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Reflection.PortableExecutable;

// Session helpers must not initialize a console: Windows terminal handoff can
// block a restricted Medium token in CSRSS before managed authentication runs.
using(var apphost=File.OpenRead(Path.Combine(AppContext.BaseDirectory,"ACTISync.Updater.exe")))using(var pe=new PEReader(apphost))if(pe.PEHeaders.PEHeader?.Subsystem!=Subsystem.WindowsGui)throw new Exception("Updater apphost must use Windows GUI subsystem for restricted session helpers");
Console.WriteLine("PASS updater apphost uses Windows GUI subsystem before any managed helper authentication");
var yandexPolicy=@"SOFTWARE\Policies\YandexBrowser\LoopbackNetworkAllowedForUrls";
if(LoopbackPolicies.PolicyOrigin(new Uri("https://acti.example"))!="https://acti.example:443" || LoopbackPolicies.PolicyOrigin(new Uri("https://acti.example:8443"))!="https://acti.example:8443" || LoopbackPolicies.PolicyOrigin(new Uri("https://[::1]"))!="https://[::1]:443")throw new Exception("Browser policy origin must include the exact HTTPS port");
if(!LoopbackPolicies.PolicyKeys.Contains(yandexPolicy) || LoopbackPolicies.PolicyKeys.Count!=3)throw new Exception("Yandex loopback provisioning missing");
var policyEntry=new LoopbackPolicies.OwnedEntry(yandexPolicy,"43127","https://acti.example");
if(!LoopbackPolicies.MayRemove(policyEntry,policyEntry.Origin) || LoopbackPolicies.MayRemove(policyEntry,"https://admin.example") || LoopbackPolicies.MayRemove(policyEntry with{Key=@"SOFTWARE\Other"},policyEntry.Origin) || LoopbackPolicies.MayRemove(policyEntry with{Slot="43126"},policyEntry.Origin) || LoopbackPolicies.MayRemove(policyEntry with{Slot="44127"},policyEntry.Origin))throw new Exception("Browser policy ownership boundary failed");
Console.WriteLine("PASS Yandex provisioning and cleanup preserve administrator entries and constrain owned registry values");
using var key=RSA.Create(3072);
var fields=new Dictionary<string,object>{["schema"]=1,["product"]="ACTISync",["version"]="1.3.1",["source_sha"]=new string('a',40),["filename"]="ACTISync-1.3.1-win-x64.msi",["sha256"]=new string('b',64),["size"]=1024,["release_tag"]="acti-sync-v1.3.1-aaaaaaaaaaaa",["published_at"]="2026-10-19T00:00:00Z"};
string Envelope(Dictionary<string,object> values) { var payload=JsonSerializer.SerializeToUtf8Bytes(values); return JsonSerializer.Serialize(new{payload=Convert.ToBase64String(payload),signature=Convert.ToBase64String(key.SignData(payload,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1))}); }
void Reject(Action action,string name) { try { action(); } catch(Exception e) when(e is InvalidDataException or JsonException or CryptographicException or FormatException) { Console.WriteLine("PASS "+name); return; } throw new Exception("Accepted "+name); }
var good=Envelope(fields);
var release=ReleaseTrust.Verify(Encoding.UTF8.GetBytes(good),key.ExportSubjectPublicKeyInfoPem());
if(release.Version!="1.3.1" || release.DownloadUri.AbsoluteUri!="https://github.com/BorisDruzak/acti_potential/releases/download/acti-sync-v1.3.1-aaaaaaaaaaaa/ACTISync-1.3.1-win-x64.msi") throw new Exception("canonical release");
Console.WriteLine("PASS signed canonical release");
using(var wrong=RSA.Create(3072)) Reject(()=>ReleaseTrust.Verify(Encoding.UTF8.GetBytes(good),wrong.ExportSubjectPublicKeyInfoPem()),"wrong signing key");
Reject(()=>ReleaseTrust.Verify(Encoding.UTF8.GetBytes(good.Replace("payload\":\"","payload\":\"AA")),key.ExportSubjectPublicKeyInfoPem()),"tampered payload");
foreach(var mutation in new (string,object)[]{("product","Other"),("version","2.0.0"),("version","1.3.1.0"),("version","1.03.1"),("version","1.65536.0"),("filename","../evil.msi"),("source_sha",new string('z',40)),("sha256",new string('a',63)),("size",0),("size",512L*1024*1024+1),("release_tag","acti-sync-v1.3.1-aaaaaaaaaaaa/evil"),("published_at","tomorrow")}) { var copy=new Dictionary<string,object>(fields){[mutation.Item1]=mutation.Item2}; Reject(()=>ReleaseTrust.Verify(Encoding.UTF8.GetBytes(Envelope(copy)),key.ExportSubjectPublicKeyInfoPem()),"invalid "+mutation.Item1+" "+mutation.Item2); }
var unknown=new Dictionary<string,object>(fields){["url"]="https://evil.invalid"}; Reject(()=>ReleaseTrust.Verify(Encoding.UTF8.GetBytes(Envelope(unknown)),key.ExportSubjectPublicKeyInfoPem()),"unknown manifest field");
Reject(()=>ReleaseTrust.Verify(Encoding.UTF8.GetBytes(good[..^1]+",\"payload\":\"AA==\"}"),key.ExportSubjectPublicKeyInfoPem()),"duplicate envelope field");
if(!release.IsNewerThan("1.3.0") || release.IsNewerThan("1.3.1") || release.IsNewerThan("1.4.0")) throw new Exception("downgrade");
foreach(var url in new[]{"http://release-assets.githubusercontent.com/a","https://evil.githubusercontent.com/a","https://github.com@evil.invalid/a","https://release-assets.githubusercontent.com:444/a"}) if(ReleaseTrust.AllowedAssetRedirect(new Uri(url))) throw new Exception("redirect accepted");
Console.WriteLine("PASS downgrade and redirect allowlist");
var handler=new ScriptedHandler(); using var client=new HttpClient(handler);
handler.Response=new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK){Content=new ByteArrayContent(new byte[9])};
await RejectAsync(async()=>await ReleaseDownload.ReadBounded(client,new Uri("https://feed.invalid/latest"),8,CancellationToken.None),"oversized HTTP response");
handler.Response=new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.Redirect){Headers={Location=new Uri("https://evil.invalid/package")}};
await RejectAsync(async()=>await ReleaseDownload.ReadBounded(client,new Uri("https://feed.invalid/latest"),8,CancellationToken.None),"feed redirect");
var temporary=Path.Combine(Path.GetTempPath(),"ACTISyncUpdaterTests-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(temporary);
try {
 var bytes=Encoding.UTF8.GetBytes("signed MSI bytes for bounded staging checks");
 var installedImage=Path.Combine(temporary,"installed-updater.exe");var helperImage=Path.Combine(temporary,"helper-updater.exe");File.WriteAllBytes(installedImage,bytes);var copyHash=HelperImage.CopyVerified(installedImage,helperImage);if(copyHash!=Convert.ToHexStringLower(SHA256.HashData(bytes)) || !File.ReadAllBytes(helperImage).SequenceEqual(bytes))throw new Exception("Helper image copy mismatch");Console.WriteLine("PASS protected helper staging copies verified installed bytes independently of MSI replacement path");
 var staged=new VerifiedRelease("1.3.1",new string('a',40),"ACTISync-1.3.1-win-x64.msi",Convert.ToHexStringLower(SHA256.HashData(bytes)),bytes.Length,"acti-sync-v1.3.1-aaaaaaaaaaaa");
 handler.Response=new(System.Net.HttpStatusCode.OK){Content=new ByteArrayContent(bytes)};
 var path=Path.Combine(temporary,"package.msi");await ReleaseDownload.Download(client,staged,path,CancellationToken.None);
 using(var verified=ReleaseDownload.VerifyFile(staged,path)) {
  RejectIo(()=>{using var writer=File.Open(path,FileMode.Open,FileAccess.Write,FileShare.ReadWrite);},"verified MSI handle blocks concurrent writes");
  RejectIo(()=>File.Delete(path),"verified MSI handle blocks replacement/deletion");
 }
 File.WriteAllBytes(path,bytes.Concat(new byte[]{1}).ToArray());Reject(()=>ReleaseDownload.VerifyFile(staged,path),"modified staged MSI size");
 File.WriteAllBytes(path,new byte[bytes.Length]);Reject(()=>ReleaseDownload.VerifyFile(staged,path),"modified staged MSI checksum");File.Delete(path);
 handler.Response=new(System.Net.HttpStatusCode.OK){Content=new ByteArrayContent(new byte[bytes.Length])};await RejectAsync(()=>ReleaseDownload.Download(client,staged,path,CancellationToken.None),"download checksum mismatch");File.Delete(path);
 handler.Response=new(System.Net.HttpStatusCode.OK){Content=new ByteArrayContent(new byte[bytes.Length+1])};await RejectAsync(()=>ReleaseDownload.Download(client,staged,path,CancellationToken.None),"asset signed-size mismatch");
 handler.Response=new(System.Net.HttpStatusCode.OK){Content=new StreamContent(new HangingStream())};await TimesOut(()=>ReleaseDownload.ReadBounded(client,new Uri("https://feed.invalid/latest"),100,CancellationToken.None,TimeSpan.FromMilliseconds(100)),"feed body absolute deadline");
 handler.Response=new(System.Net.HttpStatusCode.OK){Content=new StreamContent(new HangingStream())};await TimesOut(()=>ReleaseDownload.Download(client,staged,path,CancellationToken.None,TimeSpan.FromMilliseconds(100)),"asset body absolute deadline");
 var state=Path.Combine(temporary,"state");Directory.CreateDirectory(state);var session=Guid.NewGuid().ToString("D");var sourceDirectory=Path.Combine(temporary,"work","test-user",session);Directory.CreateDirectory(sourceDirectory);
 File.WriteAllText(Path.Combine(state,session+".json"),JsonSerializer.Serialize(new{sid="test-user",source_format="doc",session_id=session}));var source=Path.Combine(sourceDirectory,"original.doc");File.WriteAllBytes(source,bytes);
 using(var conversion=File.Open(source,FileMode.Open,FileAccess.Read,FileShare.Read))RejectIo(()=>LegacyConversionGuard.Acquire(["test-user"],temporary).Dispose(),"hung legacy conversion retains source handle and defers bootstrap");
 using(var guarded=LegacyConversionGuard.Acquire(["test-user"],temporary))RejectIo(()=>{using var conversion=File.OpenRead(source);},"drained legacy conversion cannot restart before User exit");
 if(!File.ReadAllBytes(source).SequenceEqual(bytes))throw new Exception("Legacy source bytes changed");Console.WriteLine("PASS bootstrap guard preserves immutable legacy bytes");
 var oldSession=Guid.NewGuid().ToString("D");var oldState=Path.Combine(state,oldSession+".json");var oldFolder=Path.Combine(temporary,"work","test-user",oldSession);Directory.CreateDirectory(oldFolder);var oldWorking=Path.Combine(oldFolder,"preserved.docx");File.WriteAllBytes(oldWorking,bytes);
 var oldStateBytes=JsonSerializer.SerializeToUtf8Bytes(new{sid="test-user",session_id=oldSession,finished=true,working_path=oldWorking});File.WriteAllBytes(oldState,oldStateBytes);
 using(var oldGuard=LegacyConversionGuard.Acquire(["test-user"],temporary)){}
 if(!File.ReadAllBytes(oldState).SequenceEqual(oldStateBytes) || !File.ReadAllBytes(oldWorking).SequenceEqual(bytes))throw new Exception("Old schema state or working bytes changed");Console.WriteLine("PASS finished pre-conversion-schema state defaults to DOCX without modifying state or working bytes");
 foreach(var invalidFormat in new object?[]{null,42,"unknown"}){File.WriteAllText(oldState,JsonSerializer.Serialize(new{sid="test-user",session_id=oldSession,source_format=invalidFormat,finished=true}));RejectIo(()=>LegacyConversionGuard.Acquire(["test-user"],temporary).Dispose(),"present invalid legacy source format remains rejected");}File.WriteAllBytes(oldState,oldStateBytes);
 var receipt=Path.Combine(temporary,"request.json");var original=new UpdateRequest{Version="1.3.1",Phase="installing",InstallerPid=1234,InstallerStarted=987654321,InstallerThreadId=89,Users=[new(42,321,"S-1-5-21-1",7,100)]};SecureStore.Write(receipt,original);var recovered=SecureStore.Read<UpdateRequest>(receipt)!;
 if(recovered.InstallerPid!=1234 || recovered.InstallerStarted!=987654321 || recovered.InstallerThreadId!=89 || recovered.Users.Single()!=original.Users.Single())throw new Exception("Lost interrupted install receipt");Console.WriteLine("PASS interrupted install receipt retains exact process identity and user resume set");
 using var own=System.Diagnostics.Process.GetCurrentProcess();if(!WindowsSessions.ProcessAlive(own.Id,own.StartTime.ToUniversalTime().Ticks) || WindowsSessions.ProcessAlive(own.Id,1))throw new Exception("PID reuse guard failed");Console.WriteLine("PASS recovery rejects recycled process identifiers");
 var attempted=new List<int>();var first=new UserProcess(1,1,"S-1-5-21-1",1,1);var second=new UserProcess(2,2,"S-1-5-21-2",2,2);
 if(WindowsSessions.ResumeAll([first,second],user=>{attempted.Add(user.Pid);return user.Pid!=1;}) || !attempted.SequenceEqual(new[]{1,1,2,2}))throw new Exception("A deferred user blocked another user's resume");Console.WriteLine("PASS transient resume failure does not block another session");
 attempted.Clear();if(!WindowsSessions.ResumeAll([first,second],user=>{attempted.Add(user.Pid);return true;}) || !attempted.SequenceEqual(new[]{1,1,2,2}))throw new Exception("Logged-off receipt blocked active resume");Console.WriteLine("PASS completed logged-off resume entry permits remaining sessions");
 if(WindowsSessions.ResumeDecision(first.Sid,second.Sid,true)!="complete" || WindowsSessions.ResumeDecision(first.Sid,null,false)!="complete" || WindowsSessions.ResumeDecision(first.Sid,null,null)!="defer" || WindowsSessions.ResumeDecision(first.Sid,first.Sid,true)!="launch")throw new Exception("Unsafe session reuse resume plan");Console.WriteLine("PASS session reuse never launches for replacement SID; uncertain WTS lookup defers");
 var exitTarget=new UserProcess(42,321,"S-1-5-21-1",7,100);var exitBytes=JsonSerializer.SerializeToUtf8Bytes(LegacySessionExit.WireTarget(exitTarget),ACTISync.Core.Wire.Json);
 if(LegacySessionExit.Parse(exitBytes,exitTarget.Sid,exitTarget.SessionId)!=exitTarget)throw new Exception("Legacy exit target parse");Console.WriteLine("PASS legacy session request preserves exact PID creation SID session and thread identity");
 Reject(()=>LegacySessionExit.Parse(exitBytes,"S-1-5-21-2",7),"legacy helper refuses other SID");Reject(()=>LegacySessionExit.Parse(exitBytes,exitTarget.Sid,8),"legacy helper refuses other Windows session");
 Reject(()=>LegacySessionExit.Parse(Encoding.UTF8.GetBytes("{\"pid\":42,\"started\":321,\"sid\":\"S-1-5-21-1\",\"session_id\":7,\"thread_id\":100,\"path\":\"C:\\\\evil.exe\"}"),exitTarget.Sid,7),"legacy helper refuses arbitrary path or extra fields");
 if(!LegacySessionExit.IsExpectedHelper((uint)own.Id,own.Id,own.StartTime.ToUniversalTime().Ticks) || LegacySessionExit.IsExpectedHelper((uint)own.Id+1,own.Id,own.StartTime.ToUniversalTime().Ticks) || LegacySessionExit.IsExpectedHelper((uint)own.Id,own.Id,1))throw new Exception("Legacy helper pipe accepted foreign or recycled caller");Console.WriteLine("PASS legacy service pipe authenticates exact spawned helper PID and creation identity");
 NativeQueueChecks.Run();
 await InstallerLifecycleChecks.Run(temporary);
 await LegacyPipeChecks.Run();
 await ForeignProcessChecks.Run();
 var legacyRecord=JsonSerializer.Deserialize<UserProcess>(Encoding.UTF8.GetBytes("{\"pid\":42,\"started\":321,\"sid\":\"S-1-5-21-1\",\"session_id\":7,\"thread_id\":100}"),ACTISync.Core.Wire.Json)!;if(legacyRecord.Component!="User")throw new Exception("Legacy receipt role changed");Console.WriteLine("PASS old update receipts default to the native User component");
 Reject(()=>new UserProcess(1,1,"S-1-5-21-1",1,1,"Service"),"privileged service component role");Reject(()=>JsonSerializer.Deserialize<UserProcess>("{\"component\":\"../evil\"}",ACTISync.Core.Wire.Json),"arbitrary persisted component role");
 var trayOnly=first with{Component="Tray"};var trayPlan=WindowsSessions.ResumePlan([trayOnly]);if(trayPlan.Count!=1 || trayPlan[0].Component!="Tray")throw new Exception("Tray-only resume launched a User");Console.WriteLine("PASS Tray-only resume never creates a native User process");
 var combined=WindowsSessions.ResumePlan([first,first,first with{Component="Tray"}]);if(combined.Count!=2 || combined.Count(p=>p.Component=="User")!=1 || combined.Count(p=>p.Component=="Tray")!=1)throw new Exception("Role resume deduplication failed");Console.WriteLine("PASS existing User always ensures exactly one Tray for the same SID/session");
 Reject(()=>LegacySessionExit.WireTarget(trayOnly),"legacy exit for Tray role");
 if(!WindowsSessions.AllowedToken("User",first.Sid,first.Sid,1,1,false,8192) || WindowsSessions.AllowedToken("User",first.Sid,first.Sid,1,1,false,12288) || !WindowsSessions.AllowedToken("Tray",first.Sid,first.Sid,1,1,false,12288) || WindowsSessions.AllowedToken("Tray",first.Sid,first.Sid,1,1,true,16384) || WindowsSessions.AllowedToken("Tray",first.Sid,second.Sid,1,1,false,12288) || WindowsSessions.AllowedToken("Tray",first.Sid,first.Sid,1,2,false,12288))throw new Exception("Role token boundary failed");Console.WriteLine("PASS native User requires Medium; Tray accepts only matching original non-System user context");
} finally {Directory.Delete(temporary,true);}
Console.WriteLine("All updater trust checks passed.");
async Task RejectAsync(Func<Task> action,string name) { try { await action(); } catch(InvalidDataException) { Console.WriteLine("PASS "+name); return; } throw new Exception("Accepted "+name); }
async Task TimesOut(Func<Task> action,string name) {try{await action();}catch(OperationCanceledException){Console.WriteLine("PASS "+name);return;}throw new Exception("No deadline "+name);}
void RejectIo(Action action,string name) {try{action();}catch(IOException){Console.WriteLine("PASS "+name);return;}throw new Exception("Accepted "+name);}
sealed class ScriptedHandler:HttpMessageHandler { public HttpResponseMessage Response {get;set;}=new(); protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r,CancellationToken ct)=>Task.FromResult(Response); }
sealed class HangingStream:Stream {
 public override bool CanRead=>true;public override bool CanSeek=>false;public override bool CanWrite=>false;public override long Length=>throw new NotSupportedException();public override long Position{get=>0;set=>throw new NotSupportedException();}
 public override async ValueTask<int> ReadAsync(Memory<byte> buffer,CancellationToken ct=default){await Task.Delay(Timeout.Infinite,ct);return 0;}
 public override int Read(byte[] buffer,int offset,int count)=>throw new NotSupportedException();public override void Flush(){}public override long Seek(long offset,SeekOrigin origin)=>throw new NotSupportedException();public override void SetLength(long length)=>throw new NotSupportedException();public override void Write(byte[] buffer,int offset,int count)=>throw new NotSupportedException();
}
