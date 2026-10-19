using ACTISync.Core;
using Microsoft.Extensions.Hosting;
using Microsoft.Win32;
using System.Diagnostics;
using System.Security.Cryptography;

namespace ACTISync.Updater;

internal sealed class UpdateWorker:BackgroundService {
 protected override async Task ExecuteAsync(CancellationToken stoppingToken) {
  while(!stoppingToken.IsCancellationRequested) {
   try {
    using var mutex=new Mutex(false,@"Global\ACTISync.Install.v1");
    // Kernel mutex ownership is thread-affine; hold it on this thread across
    // async downloads/drains rather than release it from an await continuation.
    var acquired=false;try{try{acquired=mutex.WaitOne(0);}catch(AbandonedMutexException){acquired=true;}if(acquired)Check(stoppingToken).GetAwaiter().GetResult();}finally{if(acquired)mutex.ReleaseMutex();}
   } catch(OperationCanceledException)when(stoppingToken.IsCancellationRequested){break;}
   catch(Exception error) {try{SecureStore.Failure("check",error);SecureStore.Status("deferred");}catch{}}
   try{await Task.Delay(TimeSpan.FromMinutes(5),stoppingToken);}catch(OperationCanceledException){break;}
  }
 }
 private static async Task Check(CancellationToken ct) {
  SecureStore.Initialize(); try{LoopbackPolicies.Provision();}catch(Exception error){SecureStore.Failure("browser_policy",error);}
  var pending=SecureStore.Read<UpdateRequest>(SecureStore.Request);
  if(pending!=null) {
   if(pending.HelperPid!=0 && WindowsSessions.ProcessAlive(pending.HelperPid,pending.HelperStarted))return;
   if(pending.Phase is "staged" or "prepared") {
    await PrepareUsers(pending,ct);pending.Phase="prepared";SecureStore.Write(SecureStore.Request,pending);
   }
   StartHelper();return;
  }
  await Bootstrap(ct);
  SecureStore.Status("checking");
  using var client=ReleaseDownload.CreateClient();
  var envelope=await ReleaseDownload.ReadBounded(client,ClientConfig.Load().Api("distribution/latest"),ReleaseTrust.MaxEnvelopeSize,ct);
  var release=ReleaseTrust.Verify(envelope,ReleaseTrust.PublicKey());
  if(!release.IsNewerThan(SecureStore.CurrentVersion)){SecureStore.Status("current");return;}
  SecureStore.Status("downloading");SecureStore.Delete(SecureStore.Package);SecureStore.WriteEnvelope(envelope);
  await ReleaseDownload.Download(client,release,SecureStore.Package,ct);
  using(var package=ReleaseDownload.VerifyFile(release,SecureStore.Package))MsiIdentity.Verify(SecureStore.Package,release);
  pending=new(){Version=release.Version,Users=WindowsSessions.Snapshot()};SecureStore.Write(SecureStore.Request,pending);
  await PrepareUsers(pending,ct);pending.Phase="prepared";SecureStore.Write(SecureStore.Request,pending);StartHelper();
 }
 private static async Task PrepareUsers(UpdateRequest request,CancellationToken ct) {
  SecureStore.Status("preparing");
  foreach(var user in request.Users)if(!await WindowsSessions.Prepare(user,ct))throw new IOException("An installed user component cannot safely drain.");
  // A new session may have logged in while other users drained. Include it in
  // the durable resume set before asking it to exit.
  for(var attempt=0;attempt<3;attempt++) {
   var newUsers=WindowsSessions.Snapshot();if(newUsers.Count==0)return;
   foreach(var user in newUsers){if(!request.Users.Contains(user)){request.Users.Add(user);SecureStore.Write(SecureStore.Request,request);}if(!await WindowsSessions.Prepare(user,ct))throw new IOException("User update deferred.");}
  }
  if(WindowsSessions.Snapshot().Count!=0)throw new IOException("User sessions are changing; update deferred.");
 }
 private static void StartHelper() {
  SecureStore.RequireSystem();HelperImage.Stage();
  var start=new ProcessStartInfo(SecureStore.HelperExe){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=SecureStore.HelperDirectory};start.ArgumentList.Add("--install");using var child=Process.Start(start)??throw new IOException();
 }
 private static async Task Bootstrap(CancellationToken ct) {
  var receipt=Path.Combine(SecureStore.Root,"bootstrap.json");var request=SecureStore.Read<UpdateRequest>(receipt);
  if(request?.Phase=="complete")return;
  if(request==null){request=new(){Users=WindowsSessions.Snapshot(),Phase="captured"};SecureStore.Write(receipt,request);}
  SecureStore.Status("bootstrapping");
  var legacy=new List<UserProcess>();
  foreach(var user in request.Users)if(WindowsSessions.StillRunning(user) && !await WindowsSessions.Prepare(user,ct))legacy.Add(user);
  if(legacy.Count>0) {
   // Old clients have no drain IPC. Pause native command delivery, allow the bounded
   // 120s conversion plus 20s open acknowledgement to settle, then post WM_QUIT.
   // Background synchronization and lease heartbeats continue throughout drain.
   // The bounded marker is released even on cancellation or a deferred exit.
   using(var pause=NativeDispatchMaintenance.Begin()) {
    await Task.Delay(TimeSpan.FromSeconds(150),ct);
    using var sources=LegacyConversionGuard.Acquire(legacy.Select(u=>u.Sid));
    foreach(var user in legacy){pause.Refresh();await WindowsSessions.LegacyExit(user,ct);}
   }
  }
  if(!WindowsSessions.ResumeAll(request.Users))throw new IOException("User session resume deferred.");
  request.Phase="complete";SecureStore.Write(receipt,request);
 }
}
