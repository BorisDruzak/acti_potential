using System;
using ACTISync.InstallerActions;
internal static class StartupHealthTests {
 internal static void Run() {
  ProcessImageTests.Run();
  var checks=0;var delays=0;
  StartupHealth.Wait(()=>++checks>3,()=>delays++,10,2);
  Program.Assert(checks==5 && delays==4,"Startup must stabilize before commit");
  foreach(var ready in new Func<bool>[]{()=>false,()=>++checks%2==0}) {
   try{StartupHealth.Wait(ready,()=>{},5,2);throw new Exception("Unhealthy installation committed");}catch(TimeoutException){}
  }
  try{StartupHealth.Wait(()=>throw new UnauthorizedAccessException(),()=>{},5,2);throw new Exception("Unsafe service accepted");}catch(UnauthorizedAccessException){}
  Console.WriteLine("PASS startup health bounded failure, stable running readiness and fail-closed identity");
 }
}
