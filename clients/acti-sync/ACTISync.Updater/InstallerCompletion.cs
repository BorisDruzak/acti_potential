using System.Runtime.ExceptionServices;
namespace ACTISync.Updater;

internal static class InstallerCompletion {
 internal static void Reconcile(bool installed,Action health,Func<bool> resume,Action waitResumed) {
  Exception? healthFailure=null;
  if(installed)try{health();}catch(Exception error){healthFailure=error;}
  // This runs only after msiexec has completed, beyond the MSI commit gate.
  // Even unhealthy committed services must never strand drained User/Tray.
  if(!resume())throw new IOException("Logged-in user resume deferred.");
  if(installed)waitResumed();
  // The caller retains the installed receipt and reports deferred on any error.
  // Its retry reconciles this transaction without launching another installer.
  if(healthFailure!=null)ExceptionDispatchInfo.Capture(healthFailure).Throw();
 }
}
