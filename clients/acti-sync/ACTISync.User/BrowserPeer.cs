using System.ComponentModel;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

namespace ACTISync.User;

// A loopback address alone does not identify a Windows user. Resolve the browser's
// outbound TCP socket and compare TokenUser and Windows session, not TokenOwner.
internal static class BrowserPeer {
 internal static bool IsCurrentUser(IPEndPoint client,int listenerPort) {
  if(!client.Address.Equals(IPAddress.Loopback)) return false;
  IntPtr table=IntPtr.Zero;
  try {
   var size=0; var result=GetExtendedTcpTable(IntPtr.Zero,ref size,false,2,5,0);
   if(result!=122 || size<4 || size>16*1024*1024) return false;
   table=Marshal.AllocHGlobal(size);
   if(GetExtendedTcpTable(table,ref size,false,2,5,0)!=0) return false;
   var count=Marshal.ReadInt32(table); var rowSize=Marshal.SizeOf<TcpRow>();
   if(count<0 || count>(size-4)/rowSize) return false;
   var caller=0u;
   for(var i=0;i<count;i++) {
    var row=Marshal.PtrToStructure<TcpRow>(table+4+i*rowSize);
    if(row.State==5 && Port(row.LocalPort)==client.Port && Port(row.RemotePort)==listenerPort && new IPAddress(row.LocalAddress).Equals(IPAddress.Loopback) && new IPAddress(row.RemoteAddress).Equals(IPAddress.Loopback)) { caller=row.Pid; break; }
   }
   if(caller==0) return false;
   using var process=OpenProcess(4096,false,caller);
   if(process.IsInvalid || !OpenProcessToken(process,8,out var token)) return false;
   using(token) {
    using var peer=new WindowsIdentity(token.DangerousGetHandle());
    using var own=WindowsIdentity.GetCurrent();
    return own.User is not null && peer.User is not null && own.User.Equals(peer.User) && ProcessIdToSessionId(caller,out var session) && session==(uint)System.Diagnostics.Process.GetCurrentProcess().SessionId;
   }
  } catch(Exception e) when(e is Win32Exception or UnauthorizedAccessException or ArgumentException) { return false; }
  finally { if(table!=IntPtr.Zero) Marshal.FreeHGlobal(table); }
 }
 private static int Port(uint value)=>(ushort)IPAddress.NetworkToHostOrder(unchecked((short)value));
 [StructLayout(LayoutKind.Sequential)] private struct TcpRow { public uint State,LocalAddress,LocalPort,RemoteAddress,RemotePort,Pid; }
 [DllImport("iphlpapi.dll")] private static extern uint GetExtendedTcpTable(IntPtr table,ref int size,bool order,int family,int kind,uint reserved);
 [DllImport("kernel32.dll",SetLastError=true)] private static extern SafeProcessHandle OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("advapi32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool OpenProcessToken(SafeProcessHandle process,uint access,out SafeAccessTokenHandle token);
 [DllImport("kernel32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool ProcessIdToSessionId(uint pid,out uint session);
}
