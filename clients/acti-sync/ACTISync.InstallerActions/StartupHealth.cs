using System;
namespace ACTISync.InstallerActions {
 internal static class StartupHealth {
  // Two seconds of consecutive readiness catches immediate startup exits.
  // The caller supplies only local SCM/file checks, never network requests.
  internal static void Wait(Func<bool> ready,Action delay,int attempts=60,int stableSamples=5) {
   int stable=0;
   for(int i=0;i<attempts;i++) {
    stable=ready()?stable+1:0;
    if(stable>=stableSamples)return;
    if(i+1<attempts)delay();
   }
   throw new TimeoutException("Installed component health failed.");
  }
 }
}
