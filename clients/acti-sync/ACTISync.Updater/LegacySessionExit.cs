using ACTISync.Core;
using System.ComponentModel;
using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;

namespace ACTISync.Updater;

// Legacy Windows message queues belong to the interactive desktop. SYSTEM in
// Session 0 cannot post WM_QUIT across that boundary. A fixed, unelevated helper
// performs only this one action after mutually authenticating the service pipe.
internal static class LegacySessionExit {
 private static string PipeName(string sid,int session)=>$"ACTISync.Update.Legacy.{sid}.{session}";
 internal static async Task Request(UserProcess target,CancellationToken ct) {
  WindowsSessions.RequireLegacyUser(target);
  SecureStore.RequireSystem();
  using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(TimeSpan.FromSeconds(30));ct=deadline.Token;
  var acl=new PipeSecurity();acl.SetAccessRuleProtection(true,false);acl.SetOwner(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null));
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid,null),PipeAccessRights.FullControl,AccessControlType.Deny));
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null),PipeAccessRights.FullControl,AccessControlType.Allow));
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(target.Sid),PipeAccessRights.ReadWrite,AccessControlType.Allow));
  using var pipe=NamedPipeServerStreamAcl.Create(PipeName(target.Sid,target.SessionId),PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,4096,4096,acl);
  using var helper=WindowsSessions.StartLegacyHelper(target);var started=helper.StartTime.ToUniversalTime().Ticks;
  while(true) {
   await pipe.WaitForConnectionAsync(ct);
   if(GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out var pid) && IsExpectedHelper(pid,helper.Id,started))break;
   pipe.Disconnect();
  }
  await Send(pipe,WireTarget(target),ct);using var response=JsonDocument.Parse(await Read(pipe,ct));var root=response.RootElement;
  ReleaseTrust.Exact(root,"ok","native_error");if(root.GetProperty("ok").ValueKind!=JsonValueKind.True){if(root.GetProperty("native_error").TryGetInt32(out var error) && error>0)throw new Win32Exception(error);throw new IOException("Legacy session helper deferred exit.");}
  await helper.WaitForExitAsync(ct);if(helper.ExitCode!=0)throw new IOException("Legacy session helper failed.");
 }
 internal static bool IsExpectedHelper(uint caller,int spawnedPid,long started)=>caller==(uint)spawnedPid && WindowsSessions.ProcessAlive(spawnedPid,started);
 internal static async Task Run() {
  using var own=WindowsIdentity.GetCurrent();using var process=Process.GetCurrentProcess();var sid=own.User?.Value;
  if(own.IsSystem || sid==null || process.SessionId==0 || !string.Equals(Path.GetFullPath(Environment.ProcessPath!),SecureStore.UpdaterExe,StringComparison.OrdinalIgnoreCase))throw new UnauthorizedAccessException();SafeFiles.NoReparse(Environment.ProcessPath!);
  using var timeout=new CancellationTokenSource(TimeSpan.FromSeconds(30));var ct=timeout.Token;
  using var pipe=CreateClient(PipeName(sid,process.SessionId));await pipe.ConnectAsync(10000,ct);
  if(!GetNamedPipeServerProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out var server) || server==0 || server!=ServicePid())throw new UnauthorizedAccessException("Legacy helper service authentication failed.");
  var bytes=await Read(pipe,ct);var target=Parse(bytes,sid,process.SessionId);
  var error=0;var ok=false;
  try {if(!WindowsSessions.StillRunning(target))ok=true;else {PostVerifiedQuit(target.Pid,target.ThreadId,()=>WindowsSessions.StillRunning(target));ok=true;}}catch(Win32Exception failure){error=failure.NativeErrorCode;}catch(IOException){}
  await Send(pipe,new{ok,native_error=error},ct);
 }
 internal static NamedPipeClientStream CreateClient(string name)=>new(".",name,PipeAccessRights.ReadWrite,PipeOptions.Asynchronous,TokenImpersonationLevel.Identification,HandleInheritability.None);
 internal static UserProcess Parse(byte[] bytes,string sid,int session) {
  using var json=JsonDocument.Parse(bytes);ReleaseTrust.Exact(json.RootElement,"pid","started","sid","session_id","thread_id");
  var target=JsonSerializer.Deserialize<UserProcess>(bytes,Wire.Json)??throw new InvalidDataException();
  if(target.Pid<=0 || target.Started<=0 || target.ThreadId==0 || target.Sid!=sid || target.SessionId!=session || session<=0)throw new InvalidDataException("Legacy exit target does not belong to this session.");return target;
 }
 internal static object WireTarget(UserProcess target){WindowsSessions.RequireLegacyUser(target);return new{target.Pid,target.Started,target.Sid,target.SessionId,target.ThreadId};}
 internal static void PostVerifiedQuit(int pid,uint threadId,Func<bool>? stillExpected=null) {
  var thread=OpenThread(0x0800|0x00100000,false,threadId);if(thread==IntPtr.Zero)throw new Win32Exception();
  try {
   if(GetProcessIdOfThread(thread)!=(uint)pid || WaitForSingleObject(thread,0)!=0x102)throw new IOException("Legacy UI thread identity changed.");
   if(stillExpected!=null && !stillExpected())throw new IOException("Legacy process identity changed.");
   var gui=new GuiInfo{Size=(uint)Marshal.SizeOf<GuiInfo>()};if(!GetGUIThreadInfo(threadId,ref gui))throw new Win32Exception();
   if(!PostThreadMessage(threadId,0x0012,UIntPtr.Zero,IntPtr.Zero))throw new Win32Exception();
  }finally{CloseHandle(thread);}
 }
 private static async Task Send<T>(Stream pipe,T value,CancellationToken ct){await pipe.WriteAsync(JsonSerializer.SerializeToUtf8Bytes(value,Wire.Json),ct);await pipe.WriteAsync(new byte[]{10},ct);await pipe.FlushAsync(ct);}
 private static async Task<byte[]> Read(Stream pipe,CancellationToken ct){var bytes=new List<byte>();var one=new byte[1];while(await pipe.ReadAsync(one,ct)>0 && one[0]!=10){if(bytes.Count>=1024)throw new InvalidDataException("Oversized legacy helper request.");bytes.Add(one[0]);}return bytes.ToArray();}
 private static uint ServicePid() {
  var manager=OpenSCManager(null,null,1);if(manager==IntPtr.Zero)throw new Win32Exception();
  try{var service=OpenService(manager,"ACTISyncUpdater",4);if(service==IntPtr.Zero)throw new Win32Exception();try{if(!QueryServiceStatusEx(service,0,out var status,Marshal.SizeOf<Status>(),out _))throw new Win32Exception();return status.State==4?status.Pid:0;}finally{CloseServiceHandle(service);}}finally{CloseServiceHandle(manager);}
 }
 [StructLayout(LayoutKind.Sequential)]private struct Status{public uint Type,State,Controls,Exit,SpecificExit,Checkpoint,Hint,Pid,Flags;}
 [StructLayout(LayoutKind.Sequential)]private struct GuiInfo{public uint Size,Flags;public IntPtr Active,Focus,Capture,MenuOwner,MoveSize,Caret;public int Left,Top,Right,Bottom;}
 [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetNamedPipeClientProcessId(IntPtr pipe,out uint pid);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetNamedPipeServerProcessId(IntPtr pipe,out uint pid);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern IntPtr OpenThread(uint access,bool inherit,uint tid);
 [DllImport("kernel32.dll")]private static extern uint GetProcessIdOfThread(IntPtr thread);
 [DllImport("kernel32.dll")]private static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
 [DllImport("kernel32.dll")]private static extern bool CloseHandle(IntPtr handle);
 [DllImport("user32.dll",SetLastError=true)]private static extern bool GetGUIThreadInfo(uint tid,ref GuiInfo info);
 [DllImport("user32.dll",EntryPoint="PostThreadMessageW",SetLastError=true)]private static extern bool PostThreadMessage(uint tid,uint message,UIntPtr wparam,IntPtr lparam);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenSCManager(string? machine,string? database,uint access);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenService(IntPtr manager,string name,uint access);
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool QueryServiceStatusEx(IntPtr service,int kind,out Status status,int size,out int needed);
 [DllImport("advapi32.dll")]private static extern bool CloseServiceHandle(IntPtr handle);
}
