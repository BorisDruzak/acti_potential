using System;
using WixToolset.Dtf.WindowsInstaller;

namespace ACTISync.InstallerActions
{
    public static class CustomActions
    {
        [CustomAction]
        public static ActionResult CheckServer(Session session)
        {
            try
            {
                var result = InstallerCheck.Run(() => session["SERVERORIGIN"], (name, value) => session[name] = value, ServerProbe.Check);
                // The probe never calls MSI APIs from its worker/certificate callback.
                // Snapshot diagnostics contain only bounded enum and boolean codes.
                foreach (var line in result.Diagnostics) session.Log("{0}", line);
                session.Log("ACTI Sync check outcome: {0}", result.Failure);
            }
            catch (Exception)
            {
                // DTF otherwise logs unhandled exception details, which may include private data.
                try
                {
                    session["ACTICHECKOK"] = string.Empty;
                    session["ACTICHECKMESSAGE"] = new ProbeResult(ProbeFailure.InvalidServer).Message;
                }
                catch (Exception) { }
            }
            return ActionResult.Success;
        }

        [CustomAction]
        public static ActionResult ValidateServerOrigin(Session session)
        {
            try
            {
                // Syntax only: unattended GPO installations and offline upgrades must not probe the network.
                if (ServerOrigin.TryNormalize(session["SERVERORIGIN"], out var origin))
                {
                    session["SERVERORIGIN"] = origin;
                    return ActionResult.Success;
                }
                using (var record = new Record(0))
                {
                    record.FormatString = new ProbeResult(ProbeFailure.InvalidUrl).Message;
                    session.Message(InstallMessage.Error, record);
                }
            }
            catch (Exception) { }
            return ActionResult.Failure;
        }
    }
}
