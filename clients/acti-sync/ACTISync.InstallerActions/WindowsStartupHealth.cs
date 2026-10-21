using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

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
  private static bool Ready(string name,string image,bool system) {
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
      using(var process=Process.GetProcessById((int)status.Pid))return string.Equals(process.MainModule?.FileName,image,StringComparison.OrdinalIgnoreCase) && !process.HasExited;
     }finally{Marshal.FreeHGlobal(buffer);}
    }finally{CloseServiceHandle(service);}
   }finally{CloseServiceHandle(manager);}
  }
  [StructLayout(LayoutKind.Sequential)]private struct Config{public uint Type,Start,Error;public IntPtr BinaryPath,Group;public uint Tag;public IntPtr Dependencies,Account,Display;}
  [StructLayout(LayoutKind.Sequential)]private struct Status{public uint Type,State,Controls,Exit,SpecificExit,Checkpoint,Hint,Pid,Flags;}
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenSCManager(string? machine,string? database,uint access);
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr OpenService(IntPtr manager,string name,uint access);
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool QueryServiceConfig(IntPtr service,IntPtr config,int size,out int needed);
  [DllImport("advapi32.dll",SetLastError=true)]private static extern bool QueryServiceStatusEx(IntPtr service,int kind,out Status status,int size,out int needed);
  [DllImport("advapi32.dll")]private static extern bool CloseServiceHandle(IntPtr handle);
 }
}
