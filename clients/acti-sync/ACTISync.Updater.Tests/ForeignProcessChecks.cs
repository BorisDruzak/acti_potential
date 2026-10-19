using ACTISync.Updater;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

internal static class ForeignProcessChecks {
 internal static async Task Run() {
  var exe=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"cmd.exe");
  var startup=new Startup{Size=Marshal.SizeOf<Startup>()};
  if(!CreateProcess(exe,new StringBuilder($"\"{exe}\" /c exit 37"),IntPtr.Zero,IntPtr.Zero,false,0x00000004|0x08000000,IntPtr.Zero,null,ref startup,out var created))throw new Win32Exception();
  try {
   using var helper=WindowsSessions.PinProcess((int)created.Pid);var started=helper.StartTime;
   if(ResumeThread(created.Thread)==uint.MaxValue)throw new Win32Exception();
   if(WaitForSingleObject(created.Process,5000)!=0)throw new Exception("Isolated foreign process did not exit");
   // Deliberately wait only after the process has already exited, matching a
   // helper that writes its final pipe response and immediately returns.
   await Task.Delay(50);await helper.WaitForExitAsync();
   if(helper.ExitCode!=37 || helper.StartTime!=started)throw new Exception("Pinned foreign process lost exit receipt");
   Console.WriteLine("PASS pinned foreign helper retains creation and exit code after response-before-wait race");
  }finally{CloseHandle(created.Thread);CloseHandle(created.Process);}
 }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private struct Startup{public int Size;public string? Reserved,Desktop,Title;public int X,Y,Width,Height,XChars,YChars,Fill,Flags;public short Show,ReservedBytes;public IntPtr ReservedPointer,Input,Output,Error;}
 [StructLayout(LayoutKind.Sequential)]private struct Created{public IntPtr Process,Thread;public uint Pid,Tid;}
 [DllImport("kernel32.dll",EntryPoint="CreateProcessW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool CreateProcess(string app,StringBuilder command,IntPtr process,IntPtr thread,bool inherit,uint flags,IntPtr environment,string? directory,ref Startup startup,out Created created);
 [DllImport("kernel32.dll",SetLastError=true)]private static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll")]private static extern uint WaitForSingleObject(IntPtr process,uint milliseconds);
 [DllImport("kernel32.dll")]private static extern bool CloseHandle(IntPtr handle);
}
