using ACTISync.Service;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddWindowsService(options => options.ServiceName = "ACTISync");
// Never log HTTP request URLs, tickets, bearer credentials or user document names.
builder.Services.AddSingleton<SyncEngine>();
builder.Services.AddHostedService<SyncWorker>();
await builder.Build().RunAsync();
