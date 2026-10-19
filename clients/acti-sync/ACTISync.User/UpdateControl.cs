using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.Json;

namespace ACTISync.User;

internal sealed class UpdateControl : IDisposable {
 private readonly CancellationTokenSource stop=new();
 private readonly Func<Task<bool>> drain;
 private readonly Action exit;
 internal UpdateControl(Func<Task<bool>> drain,Action exit) { this.drain=drain; this.exit=exit; _=Listen(); }
 private async Task Listen() {
  using var own=WindowsIdentity.GetCurrent(); var sid=own.User ?? throw new UnauthorizedAccessException();
  var name=$"ACTISync.Update.User.{sid.Value}.{System.Diagnostics.Process.GetCurrentProcess().SessionId}";
  var acl=new PipeSecurity(); acl.SetAccessRuleProtection(true,false); acl.SetOwner(sid);
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid,null),PipeAccessRights.FullControl,AccessControlType.Deny));
  acl.AddAccessRule(new PipeAccessRule(sid,PipeAccessRights.FullControl,AccessControlType.Allow));
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null),PipeAccessRights.FullControl,AccessControlType.Allow));
  while(!stop.IsCancellationRequested) {
   using var pipe=NamedPipeServerStreamAcl.Create(name,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,4096,4096,acl);
   try {
    await pipe.WaitForConnectionAsync(stop.Token);
    if(!GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(),out var caller) || caller==0 || caller!=ServiceClient.ServicePid("ACTISyncUpdater")) continue;
    using var timeout=CancellationTokenSource.CreateLinkedTokenSource(stop.Token); timeout.CancelAfter(TimeSpan.FromSeconds(180));
    var input=new List<byte>(); var one=new byte[1];
    while(await pipe.ReadAsync(one,timeout.Token)!=0 && one[0]!=10) { input.Add(one[0]); if(input.Count>128) throw new IOException(); }
    using var request=JsonDocument.Parse(input.ToArray());
    if(request.RootElement.ValueKind!=JsonValueKind.Object || request.RootElement.EnumerateObject().Count()!=1 || !request.RootElement.TryGetProperty("action",out var action) || action.GetString()!="prepare_update") continue;
    var ready=await drain().WaitAsync(timeout.Token);
    await pipe.WriteAsync(Encoding.UTF8.GetBytes(ready?"{\"ready\":true}\n":"{\"ready\":false}\n"),timeout.Token); await pipe.FlushAsync(timeout.Token);
    if(ready) { await Task.Delay(250,timeout.Token); exit(); return; }
   } catch(Exception e) when(e is IOException or OperationCanceledException or JsonException or InvalidOperationException or System.ComponentModel.Win32Exception or UnauthorizedAccessException) { if(stop.IsCancellationRequested) return; }
  }
 }
 public void Dispose() { stop.Cancel(); }
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool GetNamedPipeClientProcessId(IntPtr pipe,out uint pid);
}
