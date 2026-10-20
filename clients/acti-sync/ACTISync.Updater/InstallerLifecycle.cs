using ACTISync.Core;
using Microsoft.Extensions.Hosting;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;

namespace ACTISync.Updater;

// A deferred SYSTEM MSI action temporarily runs this NEW image under the SAME
// SCM identity accepted by installed 1.3.5 UpdateControl servers. Neither the
// ordinary helper authentication nor the installed IPC boundary is relaxed.
internal static class InstallerLifecycle {
 internal static string DirectoryPath=>Path.Combine(SecureStore.Root,"installer-lifecycle");
 internal static string StagedExe=>Path.Combine(DirectoryPath,"ACTISync.Updater.exe");
 internal static string Receipt=>Path.Combine(DirectoryPath,"resume.json");
 internal static string ControlReceipt=>Path.Combine(DirectoryPath,"control-receipt.json");
 internal static string Result=>Path.Combine(DirectoryPath,"result.txt");
 internal static readonly TimeSpan PrepareDeadline=TimeSpan.FromSeconds(175);
 private static InstallerSessionLifecycle Sessions()=>new(
  ()=>{RequireStore();var receipt=SecureStore.Read<UpdateRequest>(Receipt);if(receipt==null && File.Exists(Receipt))throw new InvalidDataException("MSI resume receipt rejected.");return receipt;},
  request=>{RequireStore();SecureStore.Write(Receipt,request);},
  ()=>{RequireStore();SecureStore.Delete(Receipt);},
  WindowsSessions.Snapshot,WindowsSessions.Prepare,users=>WindowsSessions.ResumeAll(users),()=>File.Exists(ControlReceipt));

 internal static void RequireStagedImage() {
  if(!WindowsIdentity.GetCurrent().IsSystem || !string.Equals(Environment.ProcessPath,StagedExe,StringComparison.OrdinalIgnoreCase))throw new UnauthorizedAccessException("MSI lifecycle image rejected.");
  RequireStore();RequireObject(StagedExe,false,true);
  using var image=SafeFiles.OpenRead(StagedExe);
 }
 internal static void RequireStore() {
  // Validate every mutable ancestor. ACTI may grant read access, but the
  // lifecycle directory and its files grant access only to SYSTEM/admins.
  RequireObject(Path.GetDirectoryName(SecureStore.Root)!,true,false);
  RequireObject(SecureStore.Root,true,false);
  RequireObject(DirectoryPath,true,true);
  foreach(var path in new[]{Receipt,Result,ControlReceipt})if(File.Exists(path))RequireObject(path,false,true);
 }
 private static void RequireObject(string path,bool directory,bool privateAccess) {
  SafeFiles.NoReparse(path);
  FileSystemSecurity acl=directory?new DirectoryInfo(path).GetAccessControl():new FileInfo(path).GetAccessControl();
  RequireAccess(acl,directory,privateAccess);
 }
 internal static void RequireAccess(FileSystemSecurity acl,bool directory,bool privateAccess) {
  var owner=acl.GetOwner(typeof(SecurityIdentifier))?.Value;
  if(owner is not ("S-1-5-18" or "S-1-5-32-544"))throw new UnauthorizedAccessException("MSI lifecycle owner rejected.");
  if(new RawSecurityDescriptor(acl.GetSecurityDescriptorBinaryForm(),0).DiscretionaryAcl==null)throw new UnauthorizedAccessException("MSI lifecycle DACL rejected.");
  if(directory && privateAccess && !acl.AreAccessRulesProtected)throw new UnauthorizedAccessException("MSI lifecycle inheritance rejected.");
  const FileSystemRights mutation=FileSystemRights.Write|FileSystemRights.Delete|FileSystemRights.DeleteSubdirectoriesAndFiles|FileSystemRights.ChangePermissions|FileSystemRights.TakeOwnership|(FileSystemRights)(0x10000000|0x40000000|0x02000000);
  foreach(FileSystemAccessRule rule in acl.GetAccessRules(true,true,typeof(SecurityIdentifier))) {
   if(rule.AccessControlType!=AccessControlType.Allow || rule.IdentityReference.Value is "S-1-5-18" or "S-1-5-32-544")continue;
   if(privateAccess || (rule.FileSystemRights&mutation)!=0)throw new UnauthorizedAccessException("MSI lifecycle ACL rejected.");
  }
 }
 internal static async Task RequireService(CancellationToken ct) {
  RequireStagedImage();
  await WaitForService(()=>{var status=ServiceStatus();return(status.Type,status.State,status.Pid);},token=>Task.Delay(100,token),(uint)Environment.ProcessId,ct);
 }
 internal static async Task WaitForService(Func<(uint Type,uint State,uint Pid)> query,Func<CancellationToken,Task> delay,uint expectedPid,CancellationToken ct) {
  // SCM can still be finishing OnStart when BackgroundService begins. Require
  // RUNNING before checking PID: SCM does not define its PID in START_PENDING.
  // No IPC is issued until the RUNNING identity is authenticated exactly.
  for(var i=0;i<50;i++) {
   ct.ThrowIfCancellationRequested();var status=query();
   if(status.Type!=0x10)throw new UnauthorizedAccessException("MSI lifecycle SCM type rejected.");
   if(status.State==2){await delay(ct);continue;}
   if(status.State==4 && status.Pid==expectedPid)return;
   throw new UnauthorizedAccessException("MSI lifecycle SCM identity rejected.");
  }
  throw new IOException("MSI lifecycle service startup deferred.");
 }
 internal static async Task Prepare(CancellationToken ct) {
  using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(PrepareDeadline);
  await Sessions().Prepare(deadline.Token);deadline.Token.ThrowIfCancellationRequested();WriteResult("ready");
 }
 internal static bool Recover(bool allowUncommitted=false) {
  if(!Directory.Exists(DirectoryPath))return true;
  RequireStore();
  // The MSI commit fallback can overlap normal SCM startup. Resume is bounded
  // local work and uses a separate mutex; never wait for the feed install mutex
  // owned by a self-update helper while Windows Installer is waiting for us.
  using var mutex=new Mutex(false,@"Global\ACTISync.MsiLifecycle.Resume.v1");
  var acquired=false;
  try {
   try{acquired=mutex.WaitOne(TimeSpan.FromSeconds(5));}catch(AbandonedMutexException){acquired=true;}
   if(!acquired)return false;
   if(!Sessions().Recover(allowUncommitted))return false;
   return true;
  }finally{if(acquired)mutex.ReleaseMutex();}
 }
 internal static void WriteResult(string state) {
  if(state is not ("ready" or "failed" or "resumed"))throw new InvalidDataException("MSI lifecycle result rejected.");
  RequireStore();var temporary=Path.Combine(DirectoryPath,Guid.NewGuid().ToString("N")+".new");
  try {
   using(var output=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None,4096,FileOptions.WriteThrough)){output.Write(Encoding.ASCII.GetBytes(state));output.Flush(true);}
   RequireObject(temporary,false,true);
   if(File.Exists(Result)){using var old=SafeFiles.OpenRead(Result);}
   File.Move(temporary,Result,true);
  }finally{if(File.Exists(temporary))SecureStore.Delete(temporary);}
 }
 private static Status ServiceStatus() {
  var manager=OpenSCManager(null,null,1);if(manager==IntPtr.Zero)throw new Win32Exception();
  try{var service=OpenService(manager,"ACTISyncUpdater",4);if(service==IntPtr.Zero)throw new Win32Exception();try{if(!QueryServiceStatusEx(service,0,out var status,Marshal.SizeOf<Status>(),out _))throw new Win32Exception();return status;}finally{CloseServiceHandle(service);}}finally{CloseServiceHandle(manager);}
 }
 [StructLayout(LayoutKind.Sequential)]private struct Status{public uint Type,State,Controls,Exit,SpecificExit,Checkpoint,Hint,Pid,Flags;}
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenSCManager(string? machine,string? database,uint access);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenService(IntPtr manager,string name,uint access);
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool QueryServiceStatusEx(IntPtr service,int kind,out Status status,int size,out int needed);
 [DllImport("advapi32.dll")]private static extern bool CloseServiceHandle(IntPtr handle);
}

internal sealed class InstallerLifecycleWorker(string mode):BackgroundService {
 protected override async Task ExecuteAsync(CancellationToken stoppingToken) {
  var authenticated=false;
  try {
   await InstallerLifecycle.RequireService(stoppingToken);authenticated=true;
   if(mode=="prepare")await InstallerLifecycle.Prepare(stoppingToken);
   else if(mode=="rollback") {
    if(!InstallerLifecycle.Recover(allowUncommitted:true))throw new IOException("MSI rollback resume deferred.");
    InstallerLifecycle.WriteResult("resumed");
   }else throw new InvalidDataException("Unsupported MSI lifecycle mode.");
  }catch {
   if(authenticated)try{InstallerLifecycle.WriteResult("failed");}catch{}
  }
  // A completed result is not service termination: the native action owns the
  // stop/restore operation and must not see a crashed service after ready.
  try{await Task.Delay(Timeout.Infinite,stoppingToken);}catch(OperationCanceledException)when(stoppingToken.IsCancellationRequested){}
 }
}

// Inject only local storage/session operations, retaining the production WTS
// identity checks in WindowsSessions. This seam exercises durable ordering and
// partial failures without starting installed components or an SCM service.
internal sealed class InstallerSessionLifecycle(
 Func<UpdateRequest?> read,Action<UpdateRequest> write,Action delete,
 Func<List<UserProcess>> snapshot,Func<UserProcess,CancellationToken,Task<bool>> prepare,
 Func<IEnumerable<UserProcess>,bool> resume,Func<bool>? uncommitted=null) {
 internal async Task Prepare(CancellationToken ct) {
  ct.ThrowIfCancellationRequested();
  var request=read()??new UpdateRequest{Phase="msi-captured",Users=[]};
  var initial=snapshot();foreach(var user in initial)if(!request.Users.Contains(user))request.Users.Add(user);
  // Persist the complete initial snapshot before even the first drain. Keep any
  // previous durable entries after an interrupted MSI attempt.
  write(request);
  foreach(var user in initial){ct.ThrowIfCancellationRequested();if(!await prepare(user,ct))throw new IOException("MSI user drain deferred.");}
  for(var attempt=0;attempt<3;attempt++) {
   ct.ThrowIfCancellationRequested();var remaining=snapshot();if(remaining.Count==0)return;
   // Persist the entire newly observed set before exiting any of its members.
   foreach(var user in remaining)if(!request.Users.Contains(user))request.Users.Add(user);write(request);
   foreach(var user in remaining){ct.ThrowIfCancellationRequested();if(!await prepare(user,ct))throw new IOException("MSI user drain deferred.");}
  }
  ct.ThrowIfCancellationRequested();if(snapshot().Count!=0)throw new IOException("MSI user sessions keep changing.");
 }
 internal bool Recover(bool allowUncommitted=false) {
  // Normal SCM startup occurs before InstallFinalize. Keep both components
  // stopped while files remain subject to rollback; only the authenticated
  // staged rollback service may resume restored old files before this marker
  // is removed by the native commit action.
  if(!allowUncommitted && uncommitted?.Invoke()==true)return false;
  var request=read();if(request==null)return true;
  if(!resume(request.Users))return false;
  delete();return true;
 }
}
