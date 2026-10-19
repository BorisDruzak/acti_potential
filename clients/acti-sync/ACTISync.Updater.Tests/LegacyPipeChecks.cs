using ACTISync.Updater;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;

internal static class LegacyPipeChecks {
 internal static async Task Run() {
  using var own=WindowsIdentity.GetCurrent();var sid=own.User!;var name="ACTISync.LegacyAcl.Test."+Guid.NewGuid().ToString("N");
  var acl=new PipeSecurity();acl.SetAccessRuleProtection(true,false);acl.SetOwner(sid);
  acl.AddAccessRule(new PipeAccessRule(sid,PipeAccessRights.ReadWrite,AccessControlType.Allow));
  acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null),PipeAccessRights.FullControl,AccessControlType.Allow));
  using var server=NamedPipeServerStreamAcl.Create(name,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,1024,1024,acl);
  using var client=LegacySessionExit.CreateClient(name);using var timeout=new CancellationTokenSource(TimeSpan.FromSeconds(5));var ct=timeout.Token;
  var accepting=server.WaitForConnectionAsync(ct);await client.ConnectAsync(3000,ct);await accepting;
  await client.WriteAsync(new byte[]{42},ct);var byteValue=new byte[1];if(await server.ReadAsync(byteValue,ct)!=1 || byteValue[0]!=42)throw new Exception("Legacy helper request did not cross ReadWrite-only pipe ACL");
  await server.WriteAsync(new byte[]{43},ct);if(await client.ReadAsync(byteValue,ct)!=1 || byteValue[0]!=43)throw new Exception("Legacy helper response did not cross ReadWrite-only pipe ACL");
  Console.WriteLine("PASS real legacy helper pipe exchanges bounded bytes with ReadWrite-only client ACL and explicit rights");
 }
}
