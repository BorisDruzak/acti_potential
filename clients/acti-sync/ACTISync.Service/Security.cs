using System.Security.AccessControl;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Text;

namespace ACTISync.Service;

internal static class Security {
 internal static readonly SecurityIdentifier ServiceSid = new(WellKnownSidType.LocalServiceSid, null);
 internal static void DirectoryAcl(string path, string? ownerSid = null, bool traverse = false) {
  Directory.CreateDirectory(path);
  var acl = new DirectorySecurity(); acl.SetAccessRuleProtection(true, false);
  var inheritance = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
  foreach (var sid in new[] { ServiceSid, new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null) }) acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, inheritance, PropagationFlags.None, AccessControlType.Allow));
  if (ownerSid is not null) {
   var user = new SecurityIdentifier(ownerSid);
   // Word may create/replace children for safe-save. The workspace directory itself
   // must remain service-owned and non-deletable, preventing parent-junction races.
   acl.AddAccessRule(new FileSystemAccessRule(user,FileSystemRights.ReadAndExecute | FileSystemRights.Write | FileSystemRights.Synchronize,InheritanceFlags.None,PropagationFlags.None,AccessControlType.Allow));
   acl.AddAccessRule(new FileSystemAccessRule(user,FileSystemRights.Modify | FileSystemRights.Synchronize,inheritance,PropagationFlags.InheritOnly,AccessControlType.Allow));
  }
  if (traverse) acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null), FileSystemRights.Traverse | FileSystemRights.ReadAttributes | FileSystemRights.Synchronize, InheritanceFlags.None, PropagationFlags.None, AccessControlType.Allow));
  new DirectoryInfo(path).SetAccessControl(acl);
 }
 internal static string Protect(string token) => Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(token), null, DataProtectionScope.CurrentUser));
 internal static string Unprotect(string token) => Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(token), null, DataProtectionScope.CurrentUser));
}
