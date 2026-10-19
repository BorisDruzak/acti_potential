using ACTISync.Core;
using System.ComponentModel;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text.Json;

namespace ACTISync.User;

internal static class ServiceClient {
 public static async Task<IpcResponse> Send(IpcRequest request) {
  using var timeout=new CancellationTokenSource(TimeSpan.FromSeconds(45));
  using var pipe=new NamedPipeClientStream(".",Wire.PipeName,PipeDirection.InOut,PipeOptions.Asynchronous,TokenImpersonationLevel.Impersonation);
  await pipe.ConnectAsync(3000,timeout.Token);
  // The pipe ACL authenticates callers. Verify its server against SCM too, so pipe-name squatting cannot steal a launch ticket.
  if(!GetNamedPipeServerProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out var pipePid) || pipePid!=ServicePid()) throw new UnauthorizedAccessException("Подлинность службы ACTI Sync не подтверждена.");
  await pipe.WriteAsync(JsonSerializer.SerializeToUtf8Bytes(request,Wire.Json),timeout.Token); await pipe.WriteAsync(new byte[]{10},timeout.Token); await pipe.FlushAsync(timeout.Token);
  using var reader=new StreamReader(pipe); var buffer=new char[1]; var result=new System.Text.StringBuilder();
  while(await reader.ReadAsync(buffer.AsMemory(),timeout.Token)!=0 && buffer[0]!='\n') { result.Append(buffer[0]); if(result.Length>1024*1024) throw new IOException(); }
  return JsonSerializer.Deserialize<IpcResponse>(result.ToString(),Wire.Json) ?? throw new IOException();
 }
 private static uint ServicePid() {
  var manager=OpenSCManager(null,null,1); if(manager==IntPtr.Zero) throw new Win32Exception();
  try { var service=OpenService(manager,"ACTISync",4); if(service==IntPtr.Zero) throw new Win32Exception(); try { if(!QueryServiceStatusEx(service,0,out var status,Marshal.SizeOf<ServiceStatus>(),out _)) throw new Win32Exception(); return status.ProcessId; } finally { CloseServiceHandle(service); } } finally { CloseServiceHandle(manager); }
 }
 [StructLayout(LayoutKind.Sequential)] private struct ServiceStatus { public uint ServiceType,CurrentState,ControlsAccepted,Win32ExitCode,ServiceSpecificExitCode,CheckPoint,WaitHint,ProcessId,ServiceFlags; }
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool GetNamedPipeServerProcessId(IntPtr pipe,out uint pid);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] private static extern IntPtr OpenSCManager(string? machine,string? database,uint access);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] private static extern IntPtr OpenService(IntPtr manager,string name,uint access);
 [DllImport("advapi32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool QueryServiceStatusEx(IntPtr service,int level,out ServiceStatus status,int size,out int needed);
 [DllImport("advapi32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool CloseServiceHandle(IntPtr handle);
}
