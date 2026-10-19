using ACTISync.Updater;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

try {
 if(args.Length==1 && args[0]=="--install") {SecureStore.RequireSystem(helper:true);await Installer.Run();return;}
 SecureStore.RequireSystem();
 if(args.Length==1 && args[0]=="--uninstall-cleanup") {LoopbackPolicies.Cleanup();return;}
 if(args.Length!=0)return;
 var builder=Host.CreateApplicationBuilder(args);
 builder.Services.AddWindowsService(options=>options.ServiceName="ACTISyncUpdater");
 builder.Services.AddHostedService<UpdateWorker>();
 await builder.Build().RunAsync();
} catch {Environment.ExitCode=1;}
