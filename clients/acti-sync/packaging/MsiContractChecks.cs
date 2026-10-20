// Read-only validation of the authored MSI and its custom lifecycle contract.
// ICE20 assumes a FilesInUse/Ignore UI; this installer drains authenticated ACTI
// components instead. Preserve ICE20's other guarantees explicitly below.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using WixToolset.Dtf.WindowsInstaller;

internal static class MsiContractChecks
{
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length == 2 && args[0] == "--prepare")
            {
                // WiX emits the standard empty UI table even without a ListBox
                // control. Its absence selects the documented InstallValidate
                // path; the deferred authenticated drain then owns readiness.
                using (var db = new Database(args[1], DatabaseOpenMode.Transact))
                {
                    Require(!Rows(db, "SELECT `Type` FROM `Control`").Any(r => r[0] == "ListBox"), "Cannot remove an authored ListBox.");
                    if (db.IsTablePersistent("ListBox"))
                    {
                        Require(Rows(db, "SELECT `Property` FROM `ListBox`").Count == 0, "Cannot remove populated ListBox data.");
                        using (var view = db.OpenView("DROP TABLE `ListBox`")) view.Execute();
                    }
                    db.Commit();
                }
                Console.WriteLine("Authored the guarded lifecycle UI contract; removed only the unused empty ListBox table.");
                return 0;
            }
            if (args.Length != 1) throw new InvalidOperationException("Expected one MSI path.");
            using (var db = new Database(args[0], DatabaseOpenMode.ReadOnly))
            {
                var properties = Rows(db, "SELECT `Property`, `Value` FROM `Property`").ToDictionary(r => r[0], r => r[1]);
                Require(properties["MSIRESTARTMANAGERCONTROL"] == "DisableShutdown", "Restart Manager may only detect file use.");
                Require(properties["REBOOT"] == "ReallySuppress", "Reboots must remain suppressed.");
                Require(properties["ProductLanguage"] == "1033", "Released product language must remain stable for upgrades.");
                Require(properties["UpgradeCode"].Equals("{46C46CCD-DB5A-4A73-942C-4B3E8FDF8B0D}", StringComparison.OrdinalIgnoreCase), "Upgrade identity changed.");
                Require(!db.IsTablePersistent("ListBox"), "Legacy FilesInUse scanning must not replace the guarded lifecycle.");
                var files = Rows(db, "SELECT `FileName` FROM `File`").Select(r => r[0].Split('|').Last()).OrderBy(v => v).ToArray();
                Require(files.SequenceEqual(new[] { "ACTISync.Service.exe", "ACTISync.Tray.exe", "ACTISync.Updater.exe", "ACTISync.User.exe" }), "Expected exactly the four installed executable components.");
                var components = Rows(db, "SELECT `Component`, `ComponentId` FROM `Component`").ToDictionary(r => r[0], r => r[1]);
                var stable = new Dictionary<string, string> {
                    { "Service", "{22918F35-CBB9-4AE8-B845-F10328826CE1}" },
                    { "Updater", "{676216F2-49E7-4351-8FF7-E306C393D3ED}" },
                    { "User", "{5E755634-82E1-4597-BFDE-3B4FBF68A5A4}" },
                    { "Tray", "{248F6A3C-AF4C-40BB-83BE-C7DAF2926673}" },
                    { "Configuration", "{F8996799-7DFC-47E3-AAC5-7F957C771B81}" },
                    { "DataDirectory", "{A5E71B99-B1D8-4E2D-96CE-05DA03F0D483}" }
                };
                Require(stable.All(pair => components.ContainsKey(pair.Key) && components[pair.Key].Equals(pair.Value, StringComparison.OrdinalIgnoreCase)), "Released component identities changed.");
                var media = Rows(db, "SELECT `Cabinet` FROM `Media`");
                Require(media.Count == 1 && media[0][0].StartsWith("#", StringComparison.Ordinal), "Expected one embedded cabinet.");

                var sequence = Rows(db, "SELECT `Action`, `Sequence` FROM `InstallExecuteSequence`")
                    .ToDictionary(r => r[0], r => int.Parse(r[1], CultureInfo.InvariantCulture));
                string[] ordered = { "InstallValidate", "InstallInitialize", "RollbackInstallerUsers", "PrepareInstallerUsers", "CommitInstallerUsers", "ResumeInstallerUsers", "StopServices", "InstallFiles", "InstallExecute", "RemoveExistingProducts", "InstallFinalize" };
                for (int i = 1; i < ordered.Length; i++)
                    Require(sequence[ordered[i - 1]] < sequence[ordered[i]], "Unsafe lifecycle action ordering: " + ordered[i]);
                Require(sequence["ValidateServerOrigin"] < sequence["InstallInitialize"] && !sequence.ContainsKey("CheckServer"), "Execute sequence must validate syntax without network requests.");
                var actions = Rows(db, "SELECT `Action`, `Type` FROM `CustomAction`").ToDictionary(r => r[0], r => int.Parse(r[1], CultureInfo.InvariantCulture));
                foreach (var action in new[] { "PrepareInstallerUsers", "RollbackInstallerUsers", "CommitInstallerUsers", "ResumeInstallerUsers" })
                    Require((actions[action] & 0xC00) == 0xC00, "Lifecycle action must execute deferred without impersonation: " + action);
                Require((actions["RollbackInstallerUsers"] & 0x300) == 0x100, "Rollback must precede any service mutation.");
                foreach (var action in new[] { "CommitInstallerUsers", "ResumeInstallerUsers" })
                    Require((actions[action] & 0x300) == 0x200, "Resume must wait for commit: " + action);
                Require((actions["PrepareInstallerUsers"] & 0x40) == 0, "A failed drain must abort installation.");

                // ICE20's non-FilesInUse requirements remain mandatory.
                var errorDialogs = Rows(db, "SELECT `Dialog`, `Attributes`, `Control_First` FROM `Dialog`")
                    .Where(r => (int.Parse(r[1], CultureInfo.InvariantCulture) & 0x10000) != 0).ToArray();
                Require(errorDialogs.Length == 1 && errorDialogs[0][2] == "ErrorText", "Missing accessible error dialog.");
                string errorDialog = errorDialogs[0][0];
                var controls = Rows(db, "SELECT `Dialog_`, `Control`, `Type`, `X`, `Y`, `Width`, `Height` FROM `Control`")
                    .Where(r => r[0] == errorDialog).ToDictionary(r => r[1]);
                Require(controls.ContainsKey("ErrorText") && controls["ErrorText"][2] == "Text", "Missing error message control.");
                Require(!controls.ContainsKey("ErrorIcon") || controls["ErrorIcon"][2] == "Icon", "Invalid error icon control.");
                var events = Rows(db, "SELECT `Dialog_`, `Control_`, `Event`, `Argument` FROM `ControlEvent`");
                string[] names = { "A", "C", "I", "N", "O", "R", "Y" };
                string[] values = { "ErrorAbort", "ErrorCancel", "ErrorIgnore", "ErrorNo", "ErrorOk", "ErrorRetry", "ErrorYes" };
                for (int i = 0; i < names.Length; i++)
                {
                    Require(controls.ContainsKey(names[i]) && controls[names[i]][2] == "PushButton", "Missing error response button.");
                    Require(events.Any(r => r[0] == errorDialog && r[1] == names[i] && r[2] == "EndDialog" && r[3] == values[i]), "Invalid error response event.");
                    Require(controls[names[i]].Skip(4).SequenceEqual(controls["A"].Skip(4)), "Inconsistent error response button geometry.");
                }
                foreach (var table in new[] { "AdminUISequence", "InstallUISequence" })
                {
                    var exits = Rows(db, "SELECT `Sequence` FROM `" + table + "`").Select(r => r[0]).ToArray();
                    Require(new[] { "-1", "-2", "-3" }.All(exits.Contains), "Missing success, cancel or failure exit dialog.");
                }
            }
            Console.WriteLine("MSI lifecycle, payload and replacement ICE20 UI contracts passed (read-only).");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("MSI contract validation failed: " + error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }

    private static List<string[]> Rows(Database db, string query)
    {
        var rows = new List<string[]>();
        using (var view = db.OpenView(query))
        {
            view.Execute();
            Record record;
            while ((record = view.Fetch()) != null)
                using (record) rows.Add(Enumerable.Range(1, record.FieldCount).Select(record.GetString).ToArray());
        }
        return rows;
    }
    private static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
}
