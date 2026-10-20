using ACTISync.Updater;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

try {
 if(args.Length==2 && args[0]=="--msi-lifecycle" && args[1] is "prepare" or "rollback") {
  InstallerLifecycle.RequireStagedImage();
  var lifecycle=Host.CreateApplicationBuilder(Array.Empty<string>());
  lifecycle.Services.AddWindowsService(options=>options.ServiceName="ACTISyncUpdater");
  lifecycle.Services.AddHostedService(_=>new InstallerLifecycleWorker(args[1]));
  await lifecycle.Build().RunAsync();return;
 }
 if(args.Length==1 && args[0]=="--msi-resume") {SecureStore.RequireSystem();if(!InstallerLifecycle.Recover())throw new IOException("MSI resume deferred.");return;}
 if(args.Length==1 && args[0]=="--legacy-exit") {await LegacySessionExit.Run();return;}
 if(args.Length==1 && args[0]=="--install") {SecureStore.RequireSystem(helper:true);await Installer.Run();return;}
 SecureStore.RequireSystem();
 if(args.Length==1 && args[0]=="--uninstall-cleanup") {LoopbackPolicies.Cleanup();return;}
 if(args.Length!=0)return;
 var builder=Host.CreateApplicationBuilder(args);
 builder.Services.AddWindowsService(options=>options.ServiceName="ACTISyncUpdater");
 builder.Services.AddHostedService<UpdateWorker>();
 await builder.Build().RunAsync();
} catch {Environment.ExitCode=1;}
