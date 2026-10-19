using ACTISync.Core;
using System.Security.Principal;

namespace ACTISync.User;

internal static class Program {
 [STAThread]
 private static void Main(string[] args) {
  // The updater starts this component with the original logged-in user's token.
  // It accepts no protocol, document path, command or privileged instruction.
  if(args.Length!=0) return;
  try {
   using var own=WindowsIdentity.GetCurrent();
   var session=System.Diagnostics.Process.GetCurrentProcess().SessionId;
   if(own.IsSystem || own.User is null || session==0) return;
   using var mutex=new Mutex(true,$@"Local\ACTISync.Tray.{own.User.Value}.{session}",out var first);
   if(!first) return;
   Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
   Application.ThreadException+=(_,_)=>{};
   ApplicationConfiguration.Initialize();
   using var context=new TrayContext(ClientConfig.Load().Origin);
   Application.Run(context);
  } catch { /* Desktop presence is optional; never display a management/error window. */ }
 }
}

internal sealed class TrayContext : ApplicationContext {
 private readonly AgentTray tray;
 private readonly UpdateControl updates;
 private readonly System.Windows.Forms.Timer timer=new(){Interval=4000};
 private readonly SynchronizationContext dispatch;
 private bool refreshing;
 private bool stopping;
 internal TrayContext(Uri origin) {
  SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
  dispatch=SynchronizationContext.Current!;
  tray=new(origin);
  updates=new(PrepareUpdate,()=>dispatch.Post(_=>ExitThread(),null),"Tray");
  timer.Tick+=async (_,_)=>await Refresh(); timer.Start();
  dispatch.Post(async _=>await Refresh(),null);
 }
 private Task<bool> PrepareUpdate() {
  var ready=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
  dispatch.Post(_=>{stopping=true; timer.Stop(); tray.Refresh(false,true); ready.TrySetResult(true);},null);
  return ready.Task;
 }
 private async Task Refresh() {
  if(refreshing || stopping) return;
  refreshing=true;
  try {
   var response=await ServiceClient.Presence();
   if(!stopping) tray.Refresh(response.Ok && response.AgentOnline,false);
  } catch {if(!stopping) tray.Refresh(false,false);}
  finally {refreshing=false;}
 }
 protected override void Dispose(bool disposing) {
  if(disposing) {stopping=true; timer.Dispose(); updates.Dispose(); tray.Dispose();}
  base.Dispose(disposing);
 }
}
