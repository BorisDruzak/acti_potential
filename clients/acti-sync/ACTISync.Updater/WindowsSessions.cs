using ACTISync.Core;
using Microsoft.Win32.SafeHandles;
using System.ComponentModel;
using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Text.Json;

namespace ACTISync.Updater;

internal static class WindowsSessions {
 internal static List<UserProcess> Snapshot() {
  var results=new List<UserProcess>();
  foreach(var process in Process.GetProcessesByName("ACTISync.User")) using(process) {
   try { var identity=Identify(process); if(identity!=null) results.Add(identity); } catch(Exception e) when(e is Win32Exception or InvalidOperationException) { }
  }
  return results;
 }
 private static UserProcess? Identify(Process process) {
  if(!string.Equals(process.MainModule?.FileName,SecureStore.UserExe,StringComparison.OrdinalIgnoreCase) || process.SessionId==0) return null;
  // Process.Handle requests full access. An unelevated session helper can read
  // the old user's identity while correctly lacking termination/write rights.
  using var query=OpenProcess(0x1000,false,process.Id);if(query.IsInvalid)throw new Win32Exception();
  if(!OpenProcessToken(query.DangerousGetHandle(),8,out var token)) throw new Win32Exception();
  using(token) { using var identity=new WindowsIdentity(token.DangerousGetHandle()); var sid=identity.User?.Value; if(sid is null || identity.IsSystem) return null;
   // Main thread is the earliest-created thread; never post to another process.
   var thread=process.Threads.Cast<ProcessThread>().OrderBy(t=>t.StartTime).First();
   return new(process.Id,process.StartTime.ToUniversalTime().Ticks,sid,process.SessionId,(uint)thread.Id);
  }
 }
 internal static bool StillRunning(UserProcess user) {
  try {using var process=Process.GetProcessById(user.Pid); var actual=Identify(process);return actual!=null && actual.Pid==user.Pid && actual.Started==user.Started && actual.Sid==user.Sid && actual.SessionId==user.SessionId;}catch(Exception e) when(e is ArgumentException or InvalidOperationException or Win32Exception){return false;}
 }
 internal static async Task<bool> Prepare(UserProcess user,CancellationToken ct) {
  if(!StillRunning(user)) return true;
  using var pipe=new NamedPipeClientStream(".",$"ACTISync.Update.User.{user.Sid}.{user.SessionId}",PipeDirection.InOut,PipeOptions.Asynchronous,TokenImpersonationLevel.Identification);
  try {
   await pipe.ConnectAsync(5000,ct);
   if(!GetNamedPipeServerProcessId(pipe.SafePipeHandle,out var pid) || pid!=user.Pid || !StillRunning(user)) throw new IOException("Unexpected update pipe server.");
   using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(TimeSpan.FromSeconds(165));
   using var writer=new StreamWriter(pipe,new UTF8Encoding(false),1024,leaveOpen:true){AutoFlush=true};using var reader=new StreamReader(pipe,Encoding.UTF8,false,1024,leaveOpen:true);
   await writer.WriteLineAsync("{\"action\":\"prepare_update\"}".AsMemory(),deadline.Token);
   var buffer=new char[1];var text=new StringBuilder();while(await reader.ReadAsync(buffer.AsMemory(),deadline.Token)>0 && buffer[0]!='\n'){if(text.Length>=128)throw new IOException();text.Append(buffer[0]);}
   using var reply=JsonDocument.Parse(text.ToString());ReleaseTrust.Exact(reply.RootElement,"ready");if(reply.RootElement.GetProperty("ready").ValueKind!=JsonValueKind.True)throw new IOException("User drain deferred.");
   for(var i=0;i<40 && StillRunning(user);i++)await Task.Delay(250,ct);
   if(StillRunning(user))throw new IOException("User did not exit after drain.");return true;
  } catch(TimeoutException){return false;}
 }
 internal static async Task LegacyExit(UserProcess user,CancellationToken ct) {
  if(!StillRunning(user))return;
  await LegacySessionExit.Request(user,ct);
  for(var i=0;i<40 && StillRunning(user);i++)await Task.Delay(250,ct);
  if(StillRunning(user))throw new IOException("Legacy user component did not exit; update deferred.");
 }
 internal static Process StartLegacyHelper(UserProcess user) {
  if(!WTSQueryUserToken((uint)user.SessionId,out var raw))throw new Win32Exception();
  using var token=new SafeAccessTokenHandle(raw);using var identity=new WindowsIdentity(token.DangerousGetHandle());if(identity.User?.Value!=user.Sid || identity.IsSystem)throw new UnauthorizedAccessException("Legacy session identity changed.");
  using var medium=MediumToken(token);using var actual=new WindowsIdentity(medium.DangerousGetHandle());
  if(actual.User?.Value!=user.Sid || TokenIntegrity(medium)!=0x2000 || TokenDword(medium,12)!=user.SessionId)throw new UnauthorizedAccessException("Legacy helper token rejected.");
  if(!CreateEnvironmentBlock(out var environment,medium,false))throw new Win32Exception();
  try {
   SafeFiles.NoReparse(SecureStore.UpdaterExe);var startup=new StartupInfo{Size=Marshal.SizeOf<StartupInfo>(),Desktop=@"winsta0\default"};
   // Pin the managed Process handle before the helper can finish its reply and
   // exit. GetProcessById alone keeps only a PID; ExitCode then throws after a
   // fast foreign process disappears even though IPC completed successfully.
   if(!CreateProcessAsUser(medium,SecureStore.UpdaterExe,new StringBuilder('"'+SecureStore.UpdaterExe+"\" --legacy-exit"),IntPtr.Zero,IntPtr.Zero,false,0x00000004|0x00000400|0x08000000,environment,SecureStore.InstalledDirectory,ref startup,out var created))throw new Win32Exception();
   Process? helper=null;var resumed=false;
   try{helper=PinProcess((int)created.Pid);if(ResumeThread(created.Thread)==uint.MaxValue)throw new Win32Exception();resumed=true;return helper;}
   catch{helper?.Dispose();if(!resumed)TerminateProcess(created.Process,1);throw;}
   finally{CloseHandle(created.Thread);CloseHandle(created.Process);}
  }finally{DestroyEnvironmentBlock(environment);}
 }
 internal static Process PinProcess(int pid) {var process=Process.GetProcessById(pid);try{_=process.Handle;return process;}catch{process.Dispose();throw;}}
 internal static bool Resume(UserProcess user) {
  if(Snapshot().Any(p=>p.SessionId==user.SessionId && p.Sid==user.Sid)) return true;
  if(!WTSQueryUserToken((uint)user.SessionId,out var raw))return ResumeDecision(user.Sid,null,SessionHasUser(user.SessionId))=="complete";
  using var token=new SafeAccessTokenHandle(raw);using var identity=new WindowsIdentity(token.DangerousGetHandle());
  // A session ID can be reused after logoff. Completing that old resume entry
  // must never launch in the replacement user's session or block other users.
  if(ResumeDecision(user.Sid,identity.User?.Value,true)=="complete")return true;if(identity.IsSystem)return false;
  // With UAC disabled WTS can return a full administrator token. Always derive
  // a LUA token, drop privileges/admin grants, and explicitly require Medium.
  using var medium=MediumToken(token);
  using var mediumIdentity=new WindowsIdentity(medium.DangerousGetHandle());if(mediumIdentity.User?.Value!=user.Sid || TokenIntegrity(medium)!=0x2000 || TokenDword(medium,12)!=user.SessionId)return false;
  if(!CreateEnvironmentBlock(out var environment,medium,false))throw new Win32Exception();
  try {
   SafeFiles.NoReparse(SecureStore.UserExe);
   var startup=new StartupInfo{Size=Marshal.SizeOf<StartupInfo>(),Desktop=@"winsta0\default"};
   if(!CreateProcessAsUser(medium,SecureStore.UserExe,new StringBuilder('"'+SecureStore.UserExe+'"'),IntPtr.Zero,IntPtr.Zero,false,0x00000400|0x08000000,environment,SecureStore.InstalledDirectory,ref startup,out var created))throw new Win32Exception();
   CloseHandle(created.Thread);CloseHandle(created.Process);return true;
  } finally {DestroyEnvironmentBlock(environment);}
 }
 internal static string ResumeDecision(string expectedSid,string? actualSid,bool? sessionHasUser)=>actualSid!=null?(actualSid==expectedSid?"launch":"complete"):sessionHasUser==false?"complete":"defer";
 internal static bool ResumeAll(IEnumerable<UserProcess> users,Func<UserProcess,bool>? resume=null) {
  var all=true;foreach(var user in users)try{if(!(resume??Resume)(user))all=false;}catch(Exception error){all=false;try{SecureStore.Failure("user_resume",error);}catch{}}
  return all;
 }
 private static bool? SessionHasUser(int session) {
  if(!WTSQuerySessionInformation(IntPtr.Zero,(uint)session,5,out var buffer,out var length)){var code=Marshal.GetLastWin32Error();return code is 7022 or 7007 ? false:null;}
  try{return length>2 && Marshal.ReadInt16(buffer)!=0;}finally{WTSFreeMemory(buffer);}
 }
 private static SafeAccessTokenHandle MediumToken(SafeAccessTokenHandle source) {
  if(!ConvertStringSidToSid("S-1-5-32-544",out var admin))throw new Win32Exception();
  try {
   var disabled=new SidAttributes{Sid=admin};
   if(!CreateRestrictedToken(source,0x1|0x4,1,ref disabled,0,IntPtr.Zero,0,IntPtr.Zero,out var result))throw new Win32Exception();
   try {
    if(!ConvertStringSidToSid("S-1-16-8192",out var label))throw new Win32Exception();
    try {var integrity=new SidAttributes{Sid=label,Attributes=0x20};if(!SetTokenInformation(result,25,ref integrity,Marshal.SizeOf<SidAttributes>()+(int)GetLengthSid(label)))throw new Win32Exception();}finally{LocalFree(label);}
    return result;
   }catch{result.Dispose();throw;}
  }finally{LocalFree(admin);}
 }
 private static int TokenDword(SafeAccessTokenHandle token,int kind) {var memory=Marshal.AllocHGlobal(4);try{if(!GetTokenInformation(token,kind,memory,4,out _))throw new Win32Exception();return Marshal.ReadInt32(memory);}finally{Marshal.FreeHGlobal(memory);}}
 private static int TokenIntegrity(SafeAccessTokenHandle token) {GetTokenInformation(token,25,IntPtr.Zero,0,out var length);var memory=Marshal.AllocHGlobal(length);try{if(!GetTokenInformation(token,25,memory,length,out _))throw new Win32Exception();var sid=Marshal.ReadIntPtr(memory);var count=Marshal.ReadByte(GetSidSubAuthorityCount(sid));return Marshal.ReadInt32(GetSidSubAuthority(sid,(uint)(count-1)));}finally{Marshal.FreeHGlobal(memory);}}
 internal static bool ProcessAlive(int pid,long started) {try{using var p=Process.GetProcessById(pid);return p.StartTime.ToUniversalTime().Ticks==started;}catch(ArgumentException){return false;}catch(InvalidOperationException){return false;}}
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private struct StartupInfo {public int Size;public string? Reserved,Desktop,Title;public int X,Y,XSize,YSize,XChars,YChars,Fill,Flags;public short Show,Reserved2;public IntPtr ReservedPtr,Input,Output,Error;}
 [StructLayout(LayoutKind.Sequential)]private struct ProcessInfo {public IntPtr Process,Thread;public uint Pid,Tid;}
 [StructLayout(LayoutKind.Sequential)]private struct SidAttributes {public IntPtr Sid;public uint Attributes;}
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool OpenProcessToken(IntPtr process,uint access,out SafeAccessTokenHandle token);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern SafeProcessHandle OpenProcess(uint access,bool inherit,int pid);
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool GetTokenInformation(SafeAccessTokenHandle token,int kind,IntPtr info,int size,out int length);
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool CreateRestrictedToken(SafeAccessTokenHandle token,uint flags,uint disabledCount,ref SidAttributes disabled,uint deleteCount,IntPtr deletes,uint restrictedCount,IntPtr restricts,out SafeAccessTokenHandle result);
 [DllImport("advapi32.dll",EntryPoint="ConvertStringSidToSidW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool ConvertStringSidToSid(string text,out IntPtr sid);
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool SetTokenInformation(SafeAccessTokenHandle token,int kind,ref SidAttributes data,int size);
 [DllImport("advapi32.dll")]private static extern uint GetLengthSid(IntPtr sid);
 [DllImport("kernel32.dll")]private static extern IntPtr LocalFree(IntPtr memory);
 [DllImport("advapi32.dll")]private static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
 [DllImport("advapi32.dll")]private static extern IntPtr GetSidSubAuthority(IntPtr sid,uint index);
 [DllImport("wtsapi32.dll",SetLastError=true)]private static extern bool WTSQueryUserToken(uint session,out IntPtr token);
 [DllImport("wtsapi32.dll",EntryPoint="WTSQuerySessionInformationW",SetLastError=true)]private static extern bool WTSQuerySessionInformation(IntPtr server,uint session,int kind,out IntPtr data,out int length);
 [DllImport("wtsapi32.dll")]private static extern void WTSFreeMemory(IntPtr data);
 [DllImport("userenv.dll",SetLastError=true)]private static extern bool CreateEnvironmentBlock(out IntPtr block,SafeAccessTokenHandle token,bool inherit);
 [DllImport("userenv.dll")]private static extern bool DestroyEnvironmentBlock(IntPtr block);
 [DllImport("advapi32.dll",EntryPoint="CreateProcessAsUserW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool CreateProcessAsUser(SafeAccessTokenHandle token,string app,StringBuilder command,IntPtr proc,IntPtr thread,bool inherit,uint flags,IntPtr environment,string directory,ref StartupInfo startup,out ProcessInfo result);
 [DllImport("kernel32.dll")]private static extern bool CloseHandle(IntPtr handle);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern bool TerminateProcess(IntPtr process,uint code);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe,out uint pid);
}
