using ACTISync.Updater;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

internal static class NativeQueueChecks {
 internal static void Run() {
  using var ready=new ManualResetEventSlim();uint tid=0;Exception? error=null;
  var queue=new Thread(()=> {
   var window=IntPtr.Zero;
   try {
    tid=GetCurrentThreadId();window=CreateWindowEx(0,"STATIC","ACTI updater isolated queue check",0,0,0,0,0,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero);if(window==IntPtr.Zero)throw new Win32Exception();ready.Set();
    while(GetMessage(out _,IntPtr.Zero,0,0)>0){}
   }catch(Exception failure){error=failure;ready.Set();}finally{if(window!=IntPtr.Zero)DestroyWindow(window);}
  }){IsBackground=true};queue.SetApartmentState(ApartmentState.STA);queue.Start();
  if(!ready.Wait(TimeSpan.FromSeconds(5)))throw new Exception("Native queue did not initialize");if(error!=null)throw error;
  using var own=Process.GetCurrentProcess();
  try{LegacySessionExit.PostVerifiedQuit(own.Id+1,tid);throw new Exception("Accepted wrong thread owner");}catch(IOException){Console.WriteLine("PASS legacy WM_QUIT refuses another process's thread identity");}
  if(!queue.IsAlive)throw new Exception("Rejected request stopped native queue");
  LegacySessionExit.PostVerifiedQuit(own.Id,tid);
  if(!queue.Join(TimeSpan.FromSeconds(5)) || error!=null)throw new Exception("Native queue did not exit after verified WM_QUIT",error);
  Console.WriteLine("PASS verified same-session WM_QUIT exits only the isolated native message queue");
 }
 [StructLayout(LayoutKind.Sequential)]private struct Message{public IntPtr Window;public uint Id;public UIntPtr Wparam;public IntPtr Lparam;public uint Time;public int X,Y;public uint Private;}
 [DllImport("kernel32.dll")]private static extern uint GetCurrentThreadId();
 [DllImport("user32.dll",EntryPoint="CreateWindowExW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr CreateWindowEx(uint extended,string name,string title,uint style,int x,int y,int width,int height,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr parameter);
 [DllImport("user32.dll",EntryPoint="GetMessageW",SetLastError=true)]private static extern int GetMessage(out Message message,IntPtr window,uint min,uint max);
 [DllImport("user32.dll")]private static extern bool DestroyWindow(IntPtr window);
}
