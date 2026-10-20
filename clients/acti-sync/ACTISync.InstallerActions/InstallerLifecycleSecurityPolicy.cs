using System;
using System.Security.AccessControl;
using System.Security.Principal;

namespace ACTISync.InstallerActions
{
    internal static class InstallerLifecycleSecurityPolicy
    {
        internal static void RequireAccess(RawSecurityDescriptor descriptor, bool privateAccess, bool directory)
        {
            var system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
            var administrators = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
            if (descriptor.Owner == null || (!descriptor.Owner.Equals(system) && !descriptor.Owner.Equals(administrators)) ||
                descriptor.DiscretionaryAcl == null || (descriptor.ControlFlags & ControlFlags.DiscretionaryAclPresent) == 0 ||
                (directory && privateAccess && (descriptor.ControlFlags & ControlFlags.DiscretionaryAclProtected) == 0)) throw new UnauthorizedAccessException();

            const int write = (int)(FileSystemRights.Write | FileSystemRights.Delete | FileSystemRights.DeleteSubdirectoriesAndFiles | FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership) | 0x10000000 | 0x40000000 | 0x02000000;
            foreach (GenericAce entry in descriptor.DiscretionaryAcl)
            {
                var rule = entry as CommonAce;
                if (rule == null || rule.IsCallback) throw new UnauthorizedAccessException();
                if (rule.AceQualifier == AceQualifier.AccessDenied) continue;
                if (rule.AceQualifier != AceQualifier.AccessAllowed) throw new UnauthorizedAccessException();
                if (rule.SecurityIdentifier.Equals(system) || rule.SecurityIdentifier.Equals(administrators)) continue;
                if (privateAccess || (rule.AccessMask & write) != 0) throw new UnauthorizedAccessException();
            }
        }
    }
}
