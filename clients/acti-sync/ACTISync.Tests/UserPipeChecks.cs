using ACTISync.User;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;

internal static class UserPipeChecks {
 internal static async Task Run() {
  using var identity=WindowsIdentity.GetCurrent();
  var sid=identity.User!;
  var name="ACTISync.User.Test."+Guid.NewGuid().ToString("N");
  using var server=UserProtocolPipe.CreateServer(name);
  var security=server.GetAccessControl();
  if(!sid.Equals(security.GetOwner(typeof(SecurityIdentifier)))) throw new Exception("User pipe owner must be actual account SID across UAC token owners");
  var allowed=security.GetAccessRules(true,false,typeof(SecurityIdentifier)).Cast<PipeAccessRule>().Where(x=>x.AccessControlType==AccessControlType.Allow).Select(x=>x.IdentityReference.Value).ToHashSet();
  if(!allowed.SetEquals(new[]{sid.Value,new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null).Value})) throw new Exception("User pipe ACL must not grant other accounts access");
  var impostor=new PipeSecurity(); impostor.SetOwner(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null));
  try { UserProtocolPipe.RequireOwner(impostor,sid); throw new Exception("Foreign pipe owner accepted"); } catch(UnauthorizedAccessException) { }
  using var pending=new CancellationTokenSource(TimeSpan.FromSeconds(5));
  var listening=server.WaitForConnectionAsync(pending.Token);
  using var client=UserProtocolPipe.Connect(name);
  await listening;
  using var reader=new StreamReader(server);
  var received=reader.ReadLineAsync(pending.Token);
  using var writer=new StreamWriter(client){AutoFlush=true};
  await writer.WriteLineAsync("synthetic ticket handoff");
  if(await received!="synthetic ticket handoff") throw new Exception("User protocol pipe handoff failed");
  Console.WriteLine("PASS user protocol pipe uses account SID ownership, rejects foreign owners, restricts ACL and forwards same-user requests");
 }
}
