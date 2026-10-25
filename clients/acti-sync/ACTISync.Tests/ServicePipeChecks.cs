using ACTISync.Core;
using ACTISync.Service;
using System.IO.Pipes;
using System.Reflection;
using System.Security.Principal;
using System.Text.Json;

internal static class ServicePipeChecks {
 internal static async Task Run() {
  foreach(var level in new[]{TokenImpersonationLevel.Identification,TokenImpersonationLevel.Impersonation}) {
   using var engine=new SyncEngine(Path.GetTempPath(),new ClientConfig(new Uri("https://example.com/")),new HttpClient());
   using var worker=new SyncWorker(engine);
   var name="ACTISync.Service.Test."+Guid.NewGuid().ToString("N");
   using var server=new NamedPipeServerStream(name,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous);
   using var deadline=new CancellationTokenSource(TimeSpan.FromSeconds(5));
   var listening=server.WaitForConnectionAsync(deadline.Token);
   using var client=new NamedPipeClientStream(".",name,PipeDirection.InOut,PipeOptions.Asynchronous,level);
   await client.ConnectAsync(deadline.Token); await listening;
   var serving=(Task)typeof(SyncWorker).GetMethod("Serve",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(worker,[server,deadline.Token])!;
   // A connected client may be descheduled before sending its first message.
   await Task.Delay(100,deadline.Token);
   try {
    await client.WriteAsync(JsonSerializer.SerializeToUtf8Bytes(new IpcRequest{Action="presence"},Wire.Json),deadline.Token);
    await client.WriteAsync(new byte[]{10},deadline.Token); await client.FlushAsync(deadline.Token);
    using var reader=new StreamReader(client);
    var line=await reader.ReadLineAsync(deadline.Token);
    var result=line is null ? null : JsonSerializer.Deserialize<IpcResponse>(line,Wire.Json);
    if(result?.Ok!=true) throw new Exception("Service must authenticate a delayed pipe client after receiving its message");
   } catch(IOException e) { throw new Exception("Service disconnected a delayed pipe client before reading its authenticated request",e); }
   await serving;
   Console.WriteLine("PASS service authenticates delayed "+level+" pipe caller and replies to presence");
  }
 }
}
