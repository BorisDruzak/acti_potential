using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace ACTISync.InstallerActions {
 internal static class WindowsStartupHealth {
  internal static void Check(string? expectedVersion=null) {
   var directory=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),"ACTI","Sync");
   string? installedVersion=expectedVersion;
   foreach(var role in new[]{"Service","Updater","User","Tray"}) {
    var path=Path.Combine(directory,"ACTISync."+role+".exe");
    if(!File.Exists(path) || (File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException();
    var info=FileVersionInfo.GetVersionInfo(path);var version=info.FileMajorPart+"."+info.FileMinorPart+"."+info.FileBuildPart;
    if(installedVersion==null)installedVersion=version;
    if(installedVersion!=version)throw new InvalidOperationException();
   }
   StartupHealth.Wait(()=>Ready("ACTISync",Path.Combine(directory,"ACTISync.Service.exe"),false) &&
    Ready("ACTISyncUpdater",Path.Combine(directory,"ACTISync.Updater.exe"),true),()=>Thread.Sleep(500));
  }
  internal static bool Ready(string name,string image,bool system) {
   var manager=OpenSCManager(null,null,1);if(manager==IntPtr.Zero)throw new Win32Exception();
   try {
    var service=OpenService(manager,name,1|4);if(service==IntPtr.Zero)throw new Win32Exception();
    try {
     QueryServiceConfig(service,IntPtr.Zero,0,out var size);
     if(Marshal.GetLastWin32Error()!=122 || size<=0 || size>65536)throw new InvalidOperationException();
     var buffer=Marshal.AllocHGlobal(size);
     try {
      if(!QueryServiceConfig(service,buffer,size,out _))throw new Win32Exception();var config=Marshal.PtrToStructure<Config>(buffer);
      var account=Marshal.PtrToStringUni(config.Account);var path=Marshal.PtrToStringUni(config.BinaryPath);
      if(config.Type!=0x10 || config.Start!=2 || !(string.Equals(path,"\""+image+"\"",StringComparison.OrdinalIgnoreCase) || (image.IndexOfAny(new[]{' ','\t'})<0 && string.Equals(path,image,StringComparison.OrdinalIgnoreCase))) ||
       (system?account!="LocalSystem":!string.Equals(account,"NT AUTHORITY\\LocalService",StringComparison.OrdinalIgnoreCase)))throw new UnauthorizedAccessException();
      if(!QueryServiceStatusEx(service,0,out var status,Marshal.SizeOf<Status>(),out _))throw new Win32Exception();
      if(status.State!=4 || status.Pid==0)return false;
      return ProcessMatches(status.Pid,image,()=> {
       if(!QueryServiceStatusEx(service,0,out var current,Marshal.SizeOf<Status>(),out _))throw new Win32Exception();
       return current.State==4 && current.Pid==status.Pid;
      });
     }finally{Marshal.FreeHGlobal(buffer);}
    }finally{CloseServiceHandle(service);}
   }finally{CloseServiceHandle(manager);}
  }
  internal static bool ProcessMatches(uint pid,string image,Func<bool> serviceStillOwnsProcess) {
   // MainModule enumerates modules with QUERY_INFORMATION | VM_READ. A deferred
   // MSI token need not have those rights across service identities. Pin exactly
   // one kernel process using only QUERY_LIMITED_INFORMATION; every subsequent
   // image/liveness check uses this handle, never another lookup by a reused PID.
   using(var process=OpenProcess(0x1000,false,pid)) {
    if(process.IsInvalid) {
     var error=Marshal.GetLastWin32Error();if(error==87)return false;
     throw new Win32Exception(error);
    }
    if(!Live(process))return false;
    var path=new StringBuilder(32768);var length=path.Capacity;
    if(!QueryFullProcessImageName(process,0,path,ref length))throw new Win32Exception();
    return string.Equals(path.ToString(),image,StringComparison.OrdinalIgnoreCase) && serviceStillOwnsProcess() && Live(process);
   }
  }
  private static bool Live(SafeProcessHandle process) {
   if(!GetProcessTimes(process,out var creation,out var exit,out _,out _))throw new Win32Exception();
   // Exit code259 is a legitimate completed process, so STILL_ACTIVE alone is
   // ambiguous. Kernel exit time is zero only while the pinned process is alive.
   return creation!=0 && exit==0;
  }
  [StructLayout(LayoutKind.Sequential)]private struct Config{public uint Type,Start,Error;public IntPtr BinaryPath,Group;public uint Tag;public IntPtr Dependencies,Account,Display;}
  [StructLayout(LayoutKind.Sequential)]private struct Status{public uint Type,State,Controls,Exit,SpecificExit,Checkpoint,Hint,Pid,Flags;}
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenSCManager(string? machine,string? database,uint access);
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenService(IntPtr manager,string name,uint access);
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool QueryServiceConfig(IntPtr service,IntPtr config,int size,out int needed);
  [DllImport("advapi32.dll",SetLastError=true)]private static extern bool QueryServiceStatusEx(IntPtr service,int kind,out Status status,int size,out int needed);
  [DllImport("advapi32.dll")]private static extern bool CloseServiceHandle(IntPtr handle);
  [DllImport("kernel32.dll",SetLastError=true)]private static extern SafeProcessHandle OpenProcess(uint access,bool inherit,uint pid);
  [DllImport("kernel32.dll",EntryPoint="QueryFullProcessImageNameW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool QueryFullProcessImageName(SafeProcessHandle process,uint flags,StringBuilder image,ref int length);
  [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetProcessTimes(SafeProcessHandle process,out long creation,out long exit,out long kernel,out long user);
 }
}
