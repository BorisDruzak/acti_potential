using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;

namespace ACTISync.InstallerActions
{
    internal sealed class WindowsInstallerLifecycleOperations : IInstallerLifecycleOperations
    {
        private const string ServiceName = "ACTISyncUpdater";
        private const long MaximumExecutableBytes = 256L * 1024 * 1024;
        private static readonly SecurityIdentifier SystemSid = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
        private static readonly SecurityIdentifier AdminSid = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        public bool IsSystem { get { using (var identity = WindowsIdentity.GetCurrent()) return identity.IsSystem; } }
        public string InstalledExe => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "ACTI", "Sync", "ACTISync.Updater.exe");
        private string Root => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "ACTI", "SyncUpdater", "installer-lifecycle");
        public string StagedExe => Path.Combine(Root, "ACTISync.Updater.exe");
        private string ReceiptPath => Path.Combine(Root, "control-receipt.json");
        private string ResultPath => Path.Combine(Root, "result.txt");

        public InstallerServiceState? QueryService()
        {
            using (var manager = Native.OpenManager())
            {
                var service = Native.OpenService(manager, ServiceName, 1 | 4);
                if (service.IsInvalid)
                {
                    var error = Marshal.GetLastWin32Error();
                    service.Dispose();
                    if (error == 1060) return null;
                    throw new Win32Exception(error);
                }
                using (service)
                {
                    Native.QueryServiceConfig(service, IntPtr.Zero, 0, out var needed);
                    if (Marshal.GetLastWin32Error() != 122 || needed <= 0 || needed > 65536) throw new InvalidOperationException();
                    var buffer = Marshal.AllocHGlobal(needed);
                    try
                    {
                        if (!Native.QueryServiceConfig(service, buffer, needed, out _)) throw new Win32Exception();
                        var config = Marshal.PtrToStructure<Native.Config>(buffer);
                        if (!Native.QueryServiceStatus(service, out var status)) throw new Win32Exception();
                        return new InstallerServiceState { ImagePath = Marshal.PtrToStringUni(config.BinaryPath) ?? "", Account = Marshal.PtrToStringUni(config.Account) ?? "", ServiceType = config.ServiceType, StartType = config.StartType, Running = status.CurrentState == 4, State = status.CurrentState };
                    }
                    finally { Marshal.FreeHGlobal(buffer); }
                }
            }
        }

        public bool UserComponentsExist()
        {
            foreach (var name in new[] { "ACTISync.User", "ACTISync.Tray" })
            {
                var processes = Process.GetProcessesByName(name);
                var found = processes.Length != 0;
                foreach (var process in processes) process.Dispose();
                if (found) return true;
            }
            return false;
        }

        public bool ReceiptExists()
        {
            ValidateExistingDirectories();
            if (Directory.Exists(ReceiptPath)) throw new InvalidOperationException();
            if (!File.Exists(ReceiptPath)) return false;
            ValidateFile(ReceiptPath, 4096);
            return true;
        }

        public InstallerControlReceipt ReadReceipt()
        {
            ValidateExistingDirectories();
            ValidateFile(ReceiptPath, 4096);
            return InstallerControlReceipt.Parse(ReadBoundedText(ReceiptPath, 4096), InstalledExe);
        }

        public void StageAndSave(InstallerControlReceipt receipt)
        {
            EnsureDirectories();
            if (ReceiptExists()) throw new InvalidOperationException();
            // This resource is embedded at packaging time; deferred actions cannot read MSI Binary tables.
            using (var resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("ACTISync.InstallerLifecycle.exe"))
            {
                if (resource == null || resource.Length <= 0 || resource.Length > MaximumExecutableBytes) throw new InvalidOperationException();
                AtomicWrite(StagedExe, resource, MaximumExecutableBytes);
            }
            using (var source = new MemoryStream(new UTF8Encoding(false, true).GetBytes(receipt.Serialize()))) AtomicWrite(ReceiptPath, source, 4096);
        }

        public void StopService()
        {
            RequireKnownService();
            using (var controller = new ServiceController(ServiceName))
            {
                controller.Refresh();
                if (controller.Status == ServiceControllerStatus.Stopped) return;
                if (controller.Status != ServiceControllerStatus.StopPending)
                {
                    // ServiceController.Stop() also stops dependent services. A direct SCM
                    // control targets only ACTISyncUpdater and fails if dependents prevent it.
                    using (var manager = Native.OpenManager())
                    using (var service = Native.OpenService(manager, ServiceName, 0x20))
                    {
                        if (service.IsInvalid) throw new Win32Exception();
                        if (!Native.ControlService(service, 1, out _) && Marshal.GetLastWin32Error() != 1062) throw new Win32Exception();
                    }
                }
                controller.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30));
            }
        }

        public void ChangeImagePath(string path)
        {
            RequireKnownService();
            if (!KnownPath(path)) throw new InvalidOperationException();
            using (var manager = Native.OpenManager())
            using (var service = Native.OpenService(manager, ServiceName, 2))
            {
                if (service.IsInvalid || !Native.ChangeServiceConfig(service, uint.MaxValue, uint.MaxValue, uint.MaxValue, path, null, IntPtr.Zero, null, null, null, null)) throw new Win32Exception();
            }
        }

        public void StartService()
        {
            ValidateExistingDirectories();
            var state = QueryService() ?? throw new InvalidOperationException();
            if (!InstallerLifecycle.IsTrusted(state) || !KnownPath(state.ImagePath)) throw new InvalidOperationException();
            if (!InstallerLifecycle.IsInstalledPath(state.ImagePath, InstalledExe)) ValidateFile(StagedExe, MaximumExecutableBytes);
            else
            {
                NoReparseAncestors(InstalledExe);
                var executable = new FileInfo(InstalledExe);
                if (!executable.Exists || executable.Length <= 0 || executable.Length > MaximumExecutableBytes) throw new InvalidOperationException();
                ValidateSecurity(executable.GetAccessControl(), false);
            }
            using (var controller = new ServiceController(ServiceName))
            {
                controller.Refresh();
                if (controller.Status == ServiceControllerStatus.Running) return;
                controller.Start();
                controller.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(30));
            }
        }

        public void ResetResult()
        {
            ValidateExistingDirectories();
            if (File.Exists(ResultPath)) { ValidateFile(ResultPath, 16); File.Delete(ResultPath); }
            else if (Directory.Exists(ResultPath)) throw new InvalidOperationException();
        }

        public string WaitForResult()
        {
            var elapsed = Stopwatch.StartNew();
            while (elapsed.Elapsed < TimeSpan.FromSeconds(190))
            {
                ValidateExistingDirectories();
                if (File.Exists(ResultPath))
                {
                    ValidateFile(ResultPath, 16);
                    var value = ReadBoundedText(ResultPath, 16);
                    if (value != "ready" && value != "failed" && value != "resumed") throw new InvalidOperationException();
                    return value;
                }
                if (Directory.Exists(ResultPath)) throw new InvalidOperationException();
                Thread.Sleep(250);
            }
            throw new System.TimeoutException();
        }

        public void DeleteReceipt() { ReadReceipt(); File.Delete(ReceiptPath); }

        private bool KnownPath(string path) => InstallerLifecycle.IsInstalledPath(path, InstalledExe) ||
            string.Equals(path, InstallerLifecycle.StagedPath(this, false), StringComparison.OrdinalIgnoreCase) ||
            string.Equals(path, InstallerLifecycle.StagedPath(this, true), StringComparison.OrdinalIgnoreCase);

        private void RequireKnownService()
        {
            var state = QueryService();
            if (state == null || !InstallerLifecycle.IsTrusted(state) || !KnownPath(state.ImagePath)) throw new InvalidOperationException();
        }

        private void ValidateExistingDirectories()
        {
            NoReparseAncestors(Root);
            var acti = Path.GetDirectoryName(Path.GetDirectoryName(Root))!;
            var updater = Path.GetDirectoryName(Root)!;
            foreach (var path in new[] { acti, updater, Root })
                if (Directory.Exists(path)) ValidateSecurity(new DirectoryInfo(path).GetAccessControl(), path == Root);
                else if (File.Exists(path)) throw new InvalidOperationException();
        }

        private void EnsureDirectories()
        {
            ValidateExistingDirectories();
            var acti = Path.GetDirectoryName(Path.GetDirectoryName(Root))!;
            foreach (var path in new[] { acti, Path.GetDirectoryName(Root)!, Root })
            {
                if (!Directory.Exists(path)) Directory.CreateDirectory(path, ProtectedDirectorySecurity());
                NoReparseAncestors(path);
                ValidateSecurity(new DirectoryInfo(path).GetAccessControl(), path == Root);
            }
        }

        private static DirectorySecurity ProtectedDirectorySecurity()
        {
            var security = new DirectorySecurity();
            security.SetOwner(SystemSid);
            security.SetAccessRuleProtection(true, false);
            foreach (var sid in new[] { SystemSid, AdminSid }) security.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
            return security;
        }

        private static void ValidateSecurity(FileSystemSecurity security, bool privateAccess)
        {
            InstallerLifecycleSecurityPolicy.RequireAccess(new RawSecurityDescriptor(security.GetSecurityDescriptorBinaryForm(), 0), privateAccess, security is DirectorySecurity);
        }

        private static void NoReparseAncestors(string path)
        {
            for (var current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
            {
                try { if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new UnauthorizedAccessException(); }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
            }
        }

        private static void ValidateFile(string path, long maximum)
        {
            NoReparseAncestors(path);
            var info = new FileInfo(path);
            if (!info.Exists || info.Length > maximum) throw new InvalidOperationException();
            ValidateSecurity(info.GetAccessControl(), true);
        }

        private void AtomicWrite(string destination, Stream source, long maximum)
        {
            ValidateExistingDirectories();
            if (File.Exists(destination)) ValidateFile(destination, maximum);
            else if (Directory.Exists(destination)) throw new InvalidOperationException();
            var temporary = Path.Combine(Root, Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    var buffer = new byte[65536]; long written = 0; int count;
                    while ((count = source.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        written += count; if (written > maximum) throw new InvalidOperationException();
                        output.Write(buffer, 0, count);
                    }
                    output.Flush(true);
                }
                ValidateFile(temporary, maximum);
                if (File.Exists(destination)) { ValidateFile(destination, maximum); File.Replace(temporary, destination, null); }
                else File.Move(temporary, destination);
                ValidateFile(destination, maximum);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        private static string ReadBoundedText(string path, int maximum)
        {
            using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var output = new MemoryStream())
            {
                var buffer = new byte[maximum + 1];
                int count;
                while ((count = input.Read(buffer, 0, buffer.Length)) != 0)
                {
                    if (output.Length + count > maximum) throw new InvalidOperationException();
                    output.Write(buffer, 0, count);
                }
                return new UTF8Encoding(false, true).GetString(output.ToArray());
            }
        }

        private static class Native
        {
            [StructLayout(LayoutKind.Sequential)] internal struct Config { internal uint ServiceType, StartType, ErrorControl; internal IntPtr BinaryPath, LoadOrderGroup; internal uint TagId; internal IntPtr Dependencies, Account, DisplayName; }
            [StructLayout(LayoutKind.Sequential)] internal struct Status { internal uint ServiceType, CurrentState, ControlsAccepted, Win32ExitCode, ServiceSpecificExitCode, CheckPoint, WaitHint; }
            internal sealed class ServiceHandle : Microsoft.Win32.SafeHandles.SafeHandleZeroOrMinusOneIsInvalid
            {
                private ServiceHandle() : base(true) { }
                protected override bool ReleaseHandle() => CloseServiceHandle(handle);
            }
            internal static ServiceHandle OpenManager() { var handle = OpenSCManager(null, null, 1); if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(); } return handle; }
            [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern ServiceHandle OpenSCManager(string? machineName, string? databaseName, uint access);
            [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] internal static extern ServiceHandle OpenService(ServiceHandle manager, string name, uint access);
            [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool QueryServiceConfig(ServiceHandle service, IntPtr config, int size, out int needed);
            [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool QueryServiceStatus(ServiceHandle service, out Status status);
            [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool ControlService(ServiceHandle service, uint control, out Status status);
            [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool ChangeServiceConfig(ServiceHandle service, uint serviceType, uint startType, uint errorControl, string path, string? loadOrderGroup, IntPtr tagId, string? dependencies, string? account, string? password, string? displayName);
            [DllImport("advapi32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CloseServiceHandle(IntPtr service);
        }
    }
}
