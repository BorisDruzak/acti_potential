using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;
using ACTISync.InstallerActions;

internal static class ProcessImageTests {
 internal static void Run() {
  var executable=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"cmd.exe");
  var startup=new Startup{Size=Marshal.SizeOf<Startup>()};
  if(!CreateProcess(executable,new StringBuilder("\""+executable+"\" /c exit 259"),IntPtr.Zero,IntPtr.Zero,false,0x00000004|0x08000000,IntPtr.Zero,null,ref startup,out var child))throw new Win32Exception();
  try {
   // Deny exactly the extra rights requested by MainModule. Keep limited image
   // queries available. Only this suspended, test-owned process is affected.
   if(!ConvertStringSecurityDescriptorToSecurityDescriptor("D:(D;;0x410;;;WD)(A;;0x1fffff;;;WD)",1,out var descriptor,out _))throw new Win32Exception();
   try {
    if(!GetSecurityDescriptorDacl(descriptor,out _,out var dacl,out _))throw new Win32Exception();
    var error=SetSecurityInfo(child.Process,6,4,IntPtr.Zero,IntPtr.Zero,dacl,IntPtr.Zero);if(error!=0)throw new Win32Exception((int)error);
   }finally{LocalFree(descriptor);}
   if(!OpenProcessToken(GetCurrentProcess(),2|8,out var source))throw new Win32Exception();
   using(source) {
    if(!CreateRestrictedToken(source,1,0,IntPtr.Zero,0,IntPtr.Zero,0,IntPtr.Zero,out var restricted))throw new Win32Exception();
    using(restricted)WindowsIdentity.RunImpersonated(restricted,()=> {
     var denied=false;
     try{using(var legacy=Process.GetProcessById((int)child.Pid)){var unused=legacy.MainModule;}}catch(Win32Exception error)when(error.NativeErrorCode==5){denied=true;}
     Program.Assert(denied,"Regression fixture did not reproduce MainModule access denied.");
     Console.WriteLine("PASS controlled fixture reproduces MainModule native5 with debug privilege disabled");
     Program.Assert(WindowsStartupHealth.ProcessMatches(child.Pid,executable,()=>true),"Limited image query rejected live restricted process.");
     Program.Assert(!WindowsStartupHealth.ProcessMatches(child.Pid,executable+".other",()=>true),"Wrong running image accepted.");
     Program.Assert(!WindowsStartupHealth.ProcessMatches(child.Pid,executable,()=>false),"Changed SCM PID/state accepted.");
    });
   }
   if(ResumeThread(child.Thread)==uint.MaxValue)throw new Win32Exception();
   if(WaitForSingleObject(child.Process,5000)!=0)throw new Exception("Owned image-query fixture did not exit.");
   Program.Assert(!WindowsStartupHealth.ProcessMatches(child.Pid,executable,()=>true),"Exited process with code259 accepted as running.");
   Console.WriteLine("PASS MainModule reproduces native5; limited pinned image query validates exact image, SCM recheck and exited259 safely");
  }finally {
   // Use the creation handles; never reopen or terminate an unowned PID.
   TerminateProcess(child.Process,1);CloseHandle(child.Thread);CloseHandle(child.Process);
  }
  // Read-only integration against whichever released version is installed.
  var directory=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),"ACTI","Sync");
  if(File.Exists(Path.Combine(directory,"ACTISync.Service.exe"))) {
   Program.Assert(WindowsStartupHealth.Ready("ACTISync",Path.Combine(directory,"ACTISync.Service.exe"),false),"Installed sync service image query failed.");
   Program.Assert(WindowsStartupHealth.Ready("ACTISyncUpdater",Path.Combine(directory,"ACTISync.Updater.exe"),true),"Installed updater service image query failed.");
   Console.WriteLine("PASS read-only SCM/image health for both installed ACTI services");
  }
 }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private struct Startup{public int Size;public string? Reserved,Desktop,Title;public int X,Y,Width,Height,XChars,YChars,Fill,Flags;public short Show,ReservedBytes;public IntPtr ReservedPointer,Input,Output,Error;}
 [StructLayout(LayoutKind.Sequential)]private struct Created{public IntPtr Process,Thread;public uint Pid,Tid;}
 [DllImport("kernel32.dll",EntryPoint="CreateProcessW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool CreateProcess(string app,StringBuilder command,IntPtr process,IntPtr thread,bool inherit,uint flags,IntPtr environment,string? directory,ref Startup startup,out Created created);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string text,uint version,out IntPtr descriptor,out uint size);
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool GetSecurityDescriptorDacl(IntPtr descriptor,out bool present,out IntPtr dacl,out bool defaulted);
 [DllImport("advapi32.dll")]private static extern uint SetSecurityInfo(IntPtr handle,int type,uint info,IntPtr owner,IntPtr group,IntPtr dacl,IntPtr sacl);
 [DllImport("kernel32.dll")]private static extern IntPtr LocalFree(IntPtr memory);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll")]private static extern uint WaitForSingleObject(IntPtr process,uint milliseconds);
 [DllImport("kernel32.dll")]private static extern bool TerminateProcess(IntPtr process,uint code);
 [DllImport("kernel32.dll")]private static extern bool CloseHandle(IntPtr handle);
 [DllImport("kernel32.dll")]private static extern IntPtr GetCurrentProcess();
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool OpenProcessToken(IntPtr process,uint access,out SafeAccessTokenHandle token);
 [DllImport("advapi32.dll",SetLastError=true)]private static extern bool CreateRestrictedToken(SafeAccessTokenHandle source,uint flags,uint disableCount,IntPtr disable,uint deleteCount,IntPtr deleted,uint restrictCount,IntPtr restrict,out SafeAccessTokenHandle token);
}
