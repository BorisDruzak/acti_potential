using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;

namespace ACTISync.Updater;

internal static class Installer {
 internal static Task Run()=>Task.Run(RunBlocking);
 private static void RunBlocking() {
  using var mutex=new Mutex(false,@"Global\ACTISync.Install.v1");var acquired=false;
  try {
   try{acquired=mutex.WaitOne(TimeSpan.FromSeconds(60));}catch(AbandonedMutexException){acquired=true;}if(!acquired)return;
   var request=SecureStore.Read<UpdateRequest>(SecureStore.Request);if(request==null || request.Phase=="staged")return;
   using var self=Process.GetCurrentProcess();request.HelperPid=self.Id;request.HelperStarted=self.StartTime.ToUniversalTime().Ticks;SecureStore.Write(SecureStore.Request,request);
   var release=ReleaseTrust.Verify(SecureStore.ReadEnvelope(),ReleaseTrust.PublicKey());if(release.Version!=request.Version)throw new InvalidDataException();
   // Keep the verified, single-link package handle open denying write/delete
   // while Windows Installer consumes precisely these bytes by the fixed path.
   using var package=ReleaseDownload.VerifyFile(release,SecureStore.Package);MsiIdentity.Verify(SecureStore.Package,release);
   if(request.Phase=="prepared") {
    if(!release.IsNewerThan(InstalledVersion())){request.Phase=InstalledMatches(release)?"installed":"superseded";SecureStore.Write(SecureStore.Request,request);}
    else {
     if(WindowsSessions.Snapshot().Count!=0)throw new IOException("A new user session appeared; installer deferred.");
     SecureStore.Status("installing");
     StartInstaller(request);
    }
   }
   if(request.Phase=="installing") {
    if(request.InstallerPid==0)throw new IOException("Missing installer process receipt.");
    if(WindowsSessions.ProcessAlive(request.InstallerPid,request.InstallerStarted)) {
     using var installer=WindowsSessions.PinProcess(request.InstallerPid);
     // A helper crash between durable process creation and ResumeThread leaves
     // a suspended msiexec. Resume only its exact recorded main thread.
     if(request.InstallerThreadId!=0)ResumeInstaller(request.InstallerThreadId,request.InstallerPid);
     installer.WaitForExit();request.InstallerExitCode=installer.ExitCode;
    }
    // Never relaunch an uncertain installer. Verify installed product version;
    // a failed transaction resumes users and is retried from a fresh feed poll.
    request.Phase=request.InstallerExitCode is 3010 or 1641?"reboot_required":InstalledMatches(release)?"installed":"failed";SecureStore.Write(SecureStore.Request,request);
   }
   if(!WindowsSessions.ResumeAll(request.Users))throw new IOException("Logged-in user resume deferred.");
   if(request.Phase=="installed") {
    SecureStore.Write(Path.Combine(SecureStore.Root,"last-installed.json"),new{version=release.Version,source_sha=release.SourceSha,sha256=release.Sha256,installed_at=DateTimeOffset.UtcNow,exit_code=request.InstallerExitCode});
    SecureStore.Status("current",release.Version);
   }else {
    SecureStore.Write(Path.Combine(SecureStore.Root,"last-install-result.json"),new{version=release.Version,phase=request.Phase,exit_code=request.InstallerExitCode,at=DateTimeOffset.UtcNow});
    SecureStore.Status("deferred");
   }
   SecureStore.Delete(SecureStore.Request);
  } catch(Exception error) {try{SecureStore.Failure("install",error);SecureStore.Status("deferred");}catch{};}
  finally {if(acquired)mutex.ReleaseMutex();}
 }
 private static string InstalledVersion() {var version=FileVersionInfo.GetVersionInfo(SecureStore.UserExe);return $"{version.FileMajorPart}.{version.FileMinorPart}.{version.FileBuildPart}";}
 private static bool InstalledMatches(VerifiedRelease release) {
  using var key=Registry.LocalMachine.OpenSubKey(@"SOFTWARE\ACTI\Sync");
  if(key?.GetValue("InstalledVersion")as string!=release.Version || key.GetValue("InstalledSourceSha")as string!=release.SourceSha)return false;
  foreach(var name in new[]{"User","Tray","Service","Updater"}){var file=FileVersionInfo.GetVersionInfo(Path.Combine(SecureStore.InstalledDirectory,$"ACTISync.{name}.exe"));if($"{file.FileMajorPart}.{file.FileMinorPart}.{file.FileBuildPart}"!=release.Version)return false;}
  return true;
 }
 private static void StartInstaller(UpdateRequest request) {
  var exe=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"msiexec.exe");
  var command=new StringBuilder($"\"{exe}\" /i \"{SecureStore.Package}\" /qn /norestart REBOOT=ReallySuppress MSIRESTARTMANAGERCONTROL=Disable MSIDISABLERMRESTART=1");
  var startup=new Startup{Size=Marshal.SizeOf<Startup>()};
  if(!CreateProcess(exe,command,IntPtr.Zero,IntPtr.Zero,false,0x00000004|0x08000000,IntPtr.Zero,SecureStore.Root,ref startup,out var process))throw new IOException("Cannot start Windows Installer.");
  try {
   using var child=Process.GetProcessById((int)process.Pid);request.InstallerPid=child.Id;request.InstallerStarted=child.StartTime.ToUniversalTime().Ticks;request.InstallerThreadId=process.Tid;request.Phase="installing";SecureStore.Write(SecureStore.Request,request);
   if(ResumeThread(process.Thread)==uint.MaxValue)throw new IOException("Cannot resume Windows Installer.");
  } finally {CloseHandle(process.Thread);CloseHandle(process.Process);}
 }
 private static void ResumeInstaller(uint thread,int pid) {var handle=OpenThread(0x0002|0x0800,false,thread);if(handle==IntPtr.Zero)return;try{if(GetProcessIdOfThread(handle)==(uint)pid)ResumeThread(handle);}finally{CloseHandle(handle);}}
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private struct Startup {public int Size;public string? Reserved,Desktop,Title;public int X,Y,XSize,YSize,XChars,YChars,Fill,Flags;public short Show,Reserved2;public IntPtr ReservedPtr,Input,Output,Error;}
 [StructLayout(LayoutKind.Sequential)]private struct Created {public IntPtr Process,Thread;public uint Pid,Tid;}
 [DllImport("kernel32.dll",EntryPoint="CreateProcessW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool CreateProcess(string app,StringBuilder command,IntPtr process,IntPtr thread,bool inherit,uint flags,IntPtr environment,string directory,ref Startup startup,out Created result);
 [DllImport("kernel32.dll")]private static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll")]private static extern IntPtr OpenThread(uint access,bool inherit,uint thread);
 [DllImport("kernel32.dll")]private static extern uint GetProcessIdOfThread(IntPtr thread);
 [DllImport("kernel32.dll")]private static extern bool CloseHandle(IntPtr handle);
}
