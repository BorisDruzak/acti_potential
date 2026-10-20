using System;
using System.Text;
using System.Text.RegularExpressions;

namespace ACTISync.InstallerActions
{
    internal sealed class InstallerServiceState
    {
        internal string ImagePath = "";
        internal string Account = "";
        internal uint ServiceType;
        internal uint StartType;
        internal bool Running;
        internal uint State;
    }

    internal sealed class InstallerControlReceipt
    {
        internal string ImagePath = "";
        internal uint StartType;
        internal bool Running;

        internal string Serialize() => "{\"version\":1,\"running\":" + (Running ? "true" : "false") +
            ",\"startType\":" + StartType + ",\"imagePathBase64\":\"" + Convert.ToBase64String(Encoding.Unicode.GetBytes(ImagePath)) + "\"}";

        internal static InstallerControlReceipt Parse(string value, string installedExe)
        {
            if (value.Length > 4096) throw new InvalidOperationException();
            var match = Regex.Match(value, "\\A\\{\"version\":1,\"running\":(true|false),\"startType\":([234]),\"imagePathBase64\":\"([A-Za-z0-9+/=]{1,3000})\"\\}\\z", RegexOptions.CultureInvariant);
            if (!match.Success) throw new InvalidOperationException();
            var bytes = Convert.FromBase64String(match.Groups[3].Value);
            if (bytes.Length % 2 != 0) throw new InvalidOperationException();
            var receipt = new InstallerControlReceipt { Running = match.Groups[1].Value == "true", StartType = uint.Parse(match.Groups[2].Value), ImagePath = new UnicodeEncoding(false, false, true).GetString(bytes) };
            if (!InstallerLifecycle.IsInstalledPath(receipt.ImagePath, installedExe)) throw new InvalidOperationException();
            return receipt;
        }
    }

    internal interface IInstallerLifecycleOperations
    {
        bool IsSystem { get; }
        string InstalledExe { get; }
        string StagedExe { get; }
        InstallerServiceState? QueryService();
        bool UserComponentsExist();
        bool ReceiptExists();
        InstallerControlReceipt ReadReceipt();
        void StageAndSave(InstallerControlReceipt receipt);
        void StopService();
        void ChangeImagePath(string path);
        void ResetResult();
        void StartService();
        string WaitForResult();
        void DeleteReceipt();
    }

    // Only this state machine is linked into tests. Its adapter is the sole OS mutation boundary.
    internal static class InstallerLifecycle
    {
        internal static bool IsInstalledPath(string value, string installedExe) =>
            string.Equals(value, "\"" + installedExe + "\"", StringComparison.OrdinalIgnoreCase) ||
            (installedExe.IndexOfAny(new[] { ' ', '\t', '\r', '\n', '"' }) < 0 && string.Equals(value, installedExe, StringComparison.OrdinalIgnoreCase));

        internal static string StagedPath(IInstallerLifecycleOperations operations, bool rollback) =>
            "\"" + operations.StagedExe + "\" --msi-lifecycle " + (rollback ? "rollback" : "prepare");

        internal static bool IsTrusted(InstallerServiceState state) =>
            state.ServiceType == 0x10 && state.Account == "LocalSystem" && state.StartType >= 2 && state.StartType <= 4;

        internal static void Prepare(IInstallerLifecycleOperations operations)
        {
            RequireSystem(operations);
            var service = operations.QueryService();
            if (service == null)
            {
                if (operations.ReceiptExists() || operations.UserComponentsExist()) throw new InvalidOperationException();
                return;
            }
            if (!IsTrusted(service) || service.StartType == 4 || (service.State != 1 && service.State != 4) || !IsInstalledPath(service.ImagePath, operations.InstalledExe) || operations.ReceiptExists()) throw new InvalidOperationException();
            var receipt = new InstallerControlReceipt { ImagePath = service.ImagePath, StartType = service.StartType, Running = service.Running };
            operations.StageAndSave(receipt); // Durable receipt precedes the first SCM mutation.
            try
            {
                RequireUnchanged(operations, receipt, false);
                operations.StopService();
                RequireUnchanged(operations, receipt, false);
                operations.ResetResult();
                operations.ChangeImagePath(StagedPath(operations, false));
                operations.StartService();
                if (operations.WaitForResult() != "ready") throw new InvalidOperationException();
            }
            finally { Restore(operations, receipt, false); }
        }

        internal static void Rollback(IInstallerLifecycleOperations operations)
        {
            RequireSystem(operations);
            if (!operations.ReceiptExists()) return;
            var receipt = operations.ReadReceipt();
            RequireUnchanged(operations, receipt, true);
            try
            {
                operations.StopService();
                RequireUnchanged(operations, receipt, true);
                operations.ResetResult();
                operations.ChangeImagePath(StagedPath(operations, true));
                operations.StartService();
                if (operations.WaitForResult() != "resumed") throw new InvalidOperationException();
            }
            finally { Restore(operations, receipt, true); }
            operations.DeleteReceipt();
        }

        internal static void Commit(IInstallerLifecycleOperations operations)
        {
            RequireSystem(operations);
            if (!operations.ReceiptExists()) return;
            var receipt = operations.ReadReceipt();
            // Commit cannot rewrite a newly installed service's configuration.
            var service = operations.QueryService();
            if (service != null && (!IsTrusted(service) || !IsInstalledPath(service.ImagePath, operations.InstalledExe))) throw new InvalidOperationException();
            operations.DeleteReceipt();
        }

        private static void Restore(IInstallerLifecycleOperations operations, InstallerControlReceipt receipt, bool restart)
        {
            RequireUnchanged(operations, receipt, true);
            operations.StopService();
            RequireUnchanged(operations, receipt, true);
            operations.ChangeImagePath(receipt.ImagePath);
            if (restart && receipt.Running) operations.StartService();
        }

        private static void RequireUnchanged(IInstallerLifecycleOperations operations, InstallerControlReceipt receipt, bool allowStaged)
        {
            var state = operations.QueryService();
            if (state == null || !IsTrusted(state) || state.StartType != receipt.StartType ||
                !(string.Equals(state.ImagePath, receipt.ImagePath, StringComparison.OrdinalIgnoreCase) ||
                (allowStaged && (string.Equals(state.ImagePath, StagedPath(operations, false), StringComparison.OrdinalIgnoreCase) ||
                                string.Equals(state.ImagePath, StagedPath(operations, true), StringComparison.OrdinalIgnoreCase))))) throw new InvalidOperationException();
        }

        private static void RequireSystem(IInstallerLifecycleOperations operations)
        {
            if (!operations.IsSystem) throw new UnauthorizedAccessException();
        }
    }
}
