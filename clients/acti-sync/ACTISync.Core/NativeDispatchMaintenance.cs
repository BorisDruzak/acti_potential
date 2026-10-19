using Microsoft.Win32;
using System.Globalization;
using System.Security.Principal;

namespace ACTISync.Core;

// Pause only native dispatch while old clients drain. Synchronization and lease
// heartbeats must continue: their server lease is shorter than legacy drain.
public static class NativeDispatchMaintenance {
 private const string Key=@"SOFTWARE\ACTI\Sync",Value="NativeDispatchPausedUntil";
 public static bool Active {
  get { using var key=Registry.LocalMachine.OpenSubKey(Key);return ActiveAt(key?.GetValue(Value) as string,DateTimeOffset.UtcNow); }
 }
 internal static bool ActiveAt(string? value,DateTimeOffset now)=>DateTimeOffset.TryParseExact(value,"O",CultureInfo.InvariantCulture,DateTimeStyles.None,out var until) && until>now && until<=now.AddMinutes(4);
 public static PauseScope Begin()=>new();
 public sealed class PauseScope:IDisposable {
  private string stamp="";
  internal PauseScope()=>Refresh();
  public void Refresh() {
   using var identity=WindowsIdentity.GetCurrent();
   if(!identity.IsSystem)throw new UnauthorizedAccessException("Only the updater may pause native dispatch.");
   using var key=Registry.LocalMachine.OpenSubKey(Key,true)??throw new IOException("Installed configuration is missing.");
   stamp=DateTimeOffset.UtcNow.AddMinutes(4).ToString("O",CultureInfo.InvariantCulture);key.SetValue(Value,stamp,RegistryValueKind.String);key.Flush();
  }
  public void Dispose() {
   using var key=Registry.LocalMachine.OpenSubKey(Key,true);
   if(key?.GetValue(Value) as string==stamp){key.DeleteValue(Value,false);key.Flush();}
  }
 }
}
