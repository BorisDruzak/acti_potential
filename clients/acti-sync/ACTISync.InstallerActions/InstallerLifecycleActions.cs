using System;
using System.ComponentModel;
using WixToolset.Dtf.WindowsInstaller;

namespace ACTISync.InstallerActions
{
    public static class InstallerLifecycleActions
    {
        [CustomAction]
        public static ActionResult CheckInstalledHealth(Session session) => Run(session, _ => WindowsStartupHealth.Check(session.CustomActionData["Version"]));

        [CustomAction]
        public static ActionResult PrepareInstallerUsers(Session session) => Run(session, InstallerLifecycle.Prepare);

        [CustomAction]
        public static ActionResult RollbackInstallerUsers(Session session) => Run(session, InstallerLifecycle.Rollback);

        [CustomAction]
        public static ActionResult CommitInstallerUsers(Session session) => Run(session, InstallerLifecycle.Commit);

        private static ActionResult Run(Session session, Action<IInstallerLifecycleOperations> action)
        {
            try
            {
                // No MSI properties, network endpoints, command lines, or database handles are consumed.
                action(new WindowsInstallerLifecycleOperations());
                session.Log("ACTI Sync installer lifecycle completed.");
                return ActionResult.Success;
            }
            catch (Exception error)
            {
                // Never let DTF log exception messages containing machine paths or identities.
                try
                {
                    var code = error is Win32Exception native ? native.NativeErrorCode : 0;
                    var type = error.GetType().Name;
                    session.Log("ACTI Sync installer lifecycle failed closed: type={0}; hresult={1}; native={2}.", type.Length <= 80 ? type : "Exception", error.HResult, code);
                }
                catch (Exception) { }
                return ActionResult.Failure;
            }
        }
    }
}
