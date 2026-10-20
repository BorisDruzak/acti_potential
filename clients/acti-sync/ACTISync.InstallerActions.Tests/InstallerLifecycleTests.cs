using System;
using System.Collections.Generic;
using System.Text;
using System.Security.AccessControl;
using ACTISync.InstallerActions;

internal static class InstallerLifecycleTests
{
    internal static void Run()
    {
        SecurityPolicy();
        var denied = new Fake { System = false };
        Reject(() => InstallerLifecycle.Prepare(denied));
        Reject(() => InstallerLifecycle.Rollback(denied));
        Reject(() => InstallerLifecycle.Commit(denied));
        Program.Assert(denied.Events.Count == 0, "Non-SYSTEM lifecycle accessed machine state.");

        var fresh = new Fake { Service = null };
        InstallerLifecycle.Prepare(fresh);
        Program.Assert(!fresh.Events.Contains("stage"), "Fresh install staged an unnecessary service.");
        fresh.Users = true;
        Reject(() => InstallerLifecycle.Prepare(fresh));

        foreach (var invalid in new[] { "\"C:\\other\\ACTISync.Updater.exe\"", "\"C:\\Program Files\\ACTI\\Sync\\ACTISync.Updater.exe\" --other", "C:\\Program Files\\ACTI\\Sync\\ACTISync.Updater.exe", "C:\\Program Files\\ACTI\\Sync\\ACTISync.Updater.exe --other", "\"C:\\Program Files\\ACTI\\Sync\\ACTISync.Updater.exe\" ", "\"C:\\Program Files\\ACTI\\Sync\\..\\Sync\\ACTISync.Updater.exe\"" })
        {
            var unknown = new Fake(); unknown.Service!.ImagePath = invalid;
            Reject(() => InstallerLifecycle.Prepare(unknown));
            Program.Assert(!unknown.Events.Contains("stage"), "Noncanonical service configuration was staged.");
        }
        foreach (var field in new[] { "account", "type", "disabled", "pending", "stale" })
        {
            var unknown = new Fake();
            if (field == "account") unknown.Service!.Account = "Other";
            if (field == "type") unknown.Service!.ServiceType = 0x20;
            if (field == "disabled") unknown.Service!.StartType = 4;
            if (field == "pending") unknown.Service!.State = 2;
            if (field == "stale") unknown.Receipt = new InstallerControlReceipt();
            Reject(() => InstallerLifecycle.Prepare(unknown));
            Program.Assert(!unknown.Events.Contains("stage"), "Untrusted or stale configuration was mutated.");
        }

        var ready = new Fake(); var original = ready.Service!.ImagePath;
        InstallerLifecycle.Prepare(ready);
        Program.Assert(ready.Events.IndexOf("stage") < ready.Events.IndexOf("stop"), "SCM changed before durable receipt.");
        Program.Assert(ready.Service.ImagePath == original && !ready.Service.Running && ready.Receipt!.Running, "Preparation did not preserve the original stopped service configuration.");
        ready.Result = "resumed";
        InstallerLifecycle.Rollback(ready);
        Program.Assert(ready.Service.ImagePath == original && ready.Service.Running && ready.Receipt == null, "Rollback did not restore configuration and original running state.");

        var stopped = new Fake(); stopped.Service!.Running = false; stopped.Service.State = 1;
        InstallerLifecycle.Prepare(stopped); stopped.Result = "resumed"; InstallerLifecycle.Rollback(stopped);
        Program.Assert(!stopped.Service.Running, "Rollback started a previously stopped service.");

        foreach (var failure in new[] { "failed", "timeout", "start" })
        {
            var failed = new Fake { Result = failure, ThrowStart = failure == "start" };
            Reject(() => InstallerLifecycle.Prepare(failed));
            Program.Assert(failed.Service!.ImagePath == original && !failed.Service.Running && failed.Receipt != null, "Prepare failure lost original config or rollback receipt.");
            failed.ThrowStart = false; failed.Result = "resumed"; InstallerLifecycle.Rollback(failed);
            Program.Assert(failed.Service.Running && failed.Receipt == null, "Failed preparation could not roll back.");
        }

        var drift = new Fake(); InstallerLifecycle.Prepare(drift);
        drift.Service!.ImagePath = "foreign"; drift.Events.Clear();
        Reject(() => InstallerLifecycle.Rollback(drift));
        Program.Assert(!drift.Events.Contains("stop") && !drift.Events.Contains("change"), "Rollback overwrote foreign service drift.");
        var during = new Fake { DriftOnWait = true };
        Reject(() => InstallerLifecycle.Prepare(during));
        Program.Assert(during.Service!.ImagePath == "foreign", "Prepare recovery overwrote foreign service drift.");

        var failedRollback = new Fake(); InstallerLifecycle.Prepare(failedRollback); failedRollback.Result = "failed";
        Reject(() => InstallerLifecycle.Rollback(failedRollback));
        Program.Assert(failedRollback.Service!.ImagePath == original && failedRollback.Service.Running && failedRollback.Receipt != null, "Failed rollback did not retain recoverable receipt and SCM state.");

        var noReceipt = new Fake(); InstallerLifecycle.Rollback(noReceipt);
        Program.Assert(!noReceipt.Events.Contains("query"), "Empty rollback inspected or changed a service.");
        var committed = new Fake(); InstallerLifecycle.Prepare(committed); committed.Events.Clear(); InstallerLifecycle.Commit(committed);
        Program.Assert(committed.Receipt == null && !committed.Events.Contains("stop"), "Commit changed SCM or retained transaction receipt.");

        var receipt = new InstallerControlReceipt { ImagePath = original, StartType = 2, Running = true };
        var parsed = InstallerControlReceipt.Parse(receipt.Serialize(), ready.InstalledExe);
        Program.Assert(parsed.ImagePath == original && parsed.StartType == 2 && parsed.Running, "Control receipt failed round trip.");
        foreach (var invalid in new[] { "", "{}", receipt.Serialize() + "\n", receipt.Serialize().Replace("\"version\":1", "\"version\":2"), receipt.Serialize().Replace("\"startType\":2", "\"startType\":0"), new string('x', 4097), receipt.Serialize().Replace(Convert.ToBase64String(Encoding.Unicode.GetBytes(original)), Convert.ToBase64String(Encoding.Unicode.GetBytes(original + " --other"))) })
            Reject(() => InstallerControlReceipt.Parse(invalid, ready.InstalledExe));
    }

    private static void SecurityPolicy()
    {
        const string trusted = "O:SYG:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)";
        InstallerLifecycleSecurityPolicy.RequireAccess(new RawSecurityDescriptor(trusted), true, true);
        InstallerLifecycleSecurityPolicy.RequireAccess(new RawSecurityDescriptor(trusted + "(A;OICI;FRFX;;;BU)"), false, true);
        foreach (var invalid in new[]
        {
            "O:SYG:SYD:NO_ACCESS_CONTROL", "O:SYG:SY", trusted.Replace("O:SY", "O:BU"),
            trusted.Replace("D:P", "D:"), trusted + "(A;OICI;FR;;;BU)", trusted + "(A;OICI;FA;;;WD)",
        }) Reject(() => InstallerLifecycleSecurityPolicy.RequireAccess(new RawSecurityDescriptor(invalid), true, true));
        foreach (var grant in new[] { "FW", "FA", "GW", "GA", "SD", "WD", "WO" })
            Reject(() => InstallerLifecycleSecurityPolicy.RequireAccess(new RawSecurityDescriptor(trusted + "(A;OICI;" + grant + ";;;BU)"), false, true));
        // Files inherit their private directory's DACL; they need not protect inheritance themselves.
        InstallerLifecycleSecurityPolicy.RequireAccess(new RawSecurityDescriptor(trusted.Replace("D:P", "D:")), true, false);
    }

    private static void Reject(Action action)
    {
        var rejected = false;
        try { action(); } catch (Exception) { rejected = true; }
        Program.Assert(rejected, "Unsafe lifecycle operation was accepted.");
    }

    private sealed class Fake : IInstallerLifecycleOperations
    {
        internal bool System = true, Users, ThrowStart, DriftOnWait;
        internal string Result = "ready";
        internal InstallerControlReceipt? Receipt;
        internal InstallerServiceState? Service;
        internal readonly List<string> Events = new List<string>();
        public bool IsSystem => System;
        public string InstalledExe => @"C:\Program Files\ACTI\Sync\ACTISync.Updater.exe";
        public string StagedExe => @"C:\ProgramData\ACTI\SyncUpdater\installer-lifecycle\ACTISync.Updater.exe";
        internal Fake() { Service = new InstallerServiceState { ImagePath = "\"" + InstalledExe + "\"", Account = "LocalSystem", ServiceType = 0x10, StartType = 2, Running = true, State = 4 }; }
        public InstallerServiceState? QueryService() { Events.Add("query"); return Service; }
        public bool UserComponentsExist() { Events.Add("users"); return Users; }
        public bool ReceiptExists() { Events.Add("receipt"); return Receipt != null; }
        public InstallerControlReceipt ReadReceipt() => InstallerControlReceipt.Parse(Receipt!.Serialize(), InstalledExe);
        public void StageAndSave(InstallerControlReceipt receipt) { Events.Add("stage"); Receipt = receipt; }
        public void StopService() { Events.Add("stop"); Service!.Running = false; Service.State = 1; }
        public void ChangeImagePath(string path) { Events.Add("change"); Service!.ImagePath = path; }
        public void ResetResult() { Events.Add("reset"); }
        public void StartService() { Events.Add("start"); if (ThrowStart) throw new InvalidOperationException(); Service!.Running = true; Service.State = 4; }
        public string WaitForResult() { Events.Add("wait"); if (DriftOnWait) Service!.ImagePath = "foreign"; if (Result == "timeout") throw new TimeoutException(); return Result; }
        public void DeleteReceipt() { Events.Add("delete"); Receipt = null; }
    }
}
