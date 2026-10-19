using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;

namespace ACTISync.User;

internal static class UserProtocolPipe {
 internal static NamedPipeServerStream CreateServer(string name) {
  using var identity=WindowsIdentity.GetCurrent();
  var sid=identity.User ?? throw new UnauthorizedAccessException();
  var security=new PipeSecurity(); security.SetAccessRuleProtection(true,false);
  // TokenOwner can be Administrators for the elevated half of the same account.
  // Bind both ACL and owner to TokenUser so either UAC half can hand off a ticket.
  security.SetOwner(sid);
  security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid,null),PipeAccessRights.FullControl,AccessControlType.Deny));
  security.AddAccessRule(new PipeAccessRule(sid,PipeAccessRights.FullControl,AccessControlType.Allow));
  security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null),PipeAccessRights.FullControl,AccessControlType.Allow));
  return NamedPipeServerStreamAcl.Create(name,PipeDirection.In,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,0,0,security);
 }
 internal static NamedPipeClientStream Connect(string name) {
  // Request READ_CONTROL explicitly: a write-only handle cannot inspect its owner.
  // Anonymous SQOS prevents an unverified pipe server from impersonating the caller.
  var pipe=new NamedPipeClientStream(".",name,PipeAccessRights.Write|PipeAccessRights.ReadPermissions,PipeOptions.None,TokenImpersonationLevel.Anonymous,HandleInheritability.None);
  try {
   pipe.Connect(3000);
   using var identity=WindowsIdentity.GetCurrent();
   RequireOwner(pipe.GetAccessControl(),identity.User ?? throw new UnauthorizedAccessException());
   return pipe;
  } catch { pipe.Dispose(); throw; }
 }
 internal static void RequireOwner(PipeSecurity security,SecurityIdentifier sid) {
  if(!sid.Equals(security.GetOwner(typeof(SecurityIdentifier)))) throw new UnauthorizedAccessException("Подлинность пользовательского компонента ACTI Sync не подтверждена.");
 }
}
