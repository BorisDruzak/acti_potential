using ACTISync.Core;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ACTISync.User;

internal sealed class LoopbackBridge : IDisposable {
 private readonly CancellationTokenSource stop=new();
 private readonly TcpListener? listener;
 private readonly BridgePolicy policy;
 private readonly Func<IpcRequest,Task<IpcResponse>> invoke;
 private readonly Func<bool> available;
 private static readonly JsonSerializerOptions Input=new(Wire.Json){UnmappedMemberHandling=JsonUnmappedMemberHandling.Disallow};
 internal int Port {get;}
 internal LoopbackBridge(Uri origin,Func<IpcRequest,Task<IpcResponse>> invoke,Func<bool> available) {
  policy=new(origin); this.invoke=invoke; this.available=available;
  for(var port=43127;port<=43142;port++) {
   var candidate=new TcpListener(IPAddress.Loopback,port); candidate.Server.ExclusiveAddressUse=true;
   try { candidate.Start(16); listener=candidate; Port=port; break; }
   catch(SocketException) { candidate.Stop(); }
  }
  if(listener is not null) _=Accept();
 }
 private async Task Accept() {
  using var slots=new SemaphoreSlim(16);
  try {
   while(!stop.IsCancellationRequested) {
    var client=await listener!.AcceptTcpClientAsync(stop.Token);
    if(!await slots.WaitAsync(0,stop.Token)) { client.Dispose(); continue; }
    _=ServeBounded(client,slots);
   }
  } catch(Exception e) when(e is OperationCanceledException or SocketException or ObjectDisposedException) { }
 }
 private async Task ServeBounded(TcpClient client,SemaphoreSlim slots) { try { await Serve(client); } finally { try { slots.Release(); } catch(ObjectDisposedException) { } } }
 private async Task Serve(TcpClient client) {
  using(client) using(var timeout=CancellationTokenSource.CreateLinkedTokenSource(stop.Token)) {
   timeout.CancelAfter(TimeSpan.FromSeconds(45));
   try {
    var peer=client.Client.RemoteEndPoint as IPEndPoint;
    if(peer is null || !BrowserPeer.IsCurrentUser(peer,Port)) return;
    var stream=client.GetStream(); var header=new List<byte>(); var one=new byte[1];
    while(await stream.ReadAsync(one,timeout.Token)!=0) {
     header.Add(one[0]); if(header.Count>8192) return;
     if(header.Count>=4 && header[^4]==13 && header[^3]==10 && header[^2]==13 && header[^1]==10) break;
    }
    var lines=Encoding.ASCII.GetString(header.ToArray()).Split("\r\n",StringSplitOptions.None);
    var first=lines[0].Split(' '); if(first.Length!=3 || first[2]!="HTTP/1.1") return;
    var headers=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
    foreach(var line in lines.Skip(1).Where(s=>s.Length!=0)) { var colon=line.IndexOf(':'); if(colon<1 || !headers.TryAdd(line[..colon],line[(colon+1)..].Trim())) return; }
    if(!headers.TryGetValue("Host",out var host) || host!=$"127.0.0.1:{Port}" || headers.ContainsKey("Transfer-Encoding") || !headers.TryGetValue("Origin",out var origin) || !policy.AcceptOrigin(origin)) return;
    if(!available()) { await Reply(stream,503,origin,new{ok=false},timeout.Token); return; }
    var path=first[1];
    if(first[0]=="OPTIONS" && path is "/v1/status" or "/v1/connect" or "/v1/launch") { await Reply(stream,204,origin,null,timeout.Token); return; }
    if(first[0]=="GET" && path=="/v1/status") { await Reply(stream,200,origin,new{version=Wire.Version,challenge=policy.Issue(DateTimeOffset.UtcNow)},timeout.Token); return; }
    if(first[0]!="POST" || path is not ("/v1/connect" or "/v1/launch") || !headers.TryGetValue("Content-Type",out var contentType) || contentType!="application/json" || !headers.TryGetValue("Content-Length",out var length) || !int.TryParse(length,out var size) || size<1 || size>1024) { await Reply(stream,400,origin,new{ok=false},timeout.Token); return; }
    var bytes=new byte[size]; await stream.ReadExactlyAsync(bytes,timeout.Token);
    var request=JsonSerializer.Deserialize<TicketRequest>(bytes,Input);
    if(request is null || !policy.Consume(request.Challenge,request.Ticket,DateTimeOffset.UtcNow)) { await Reply(stream,403,origin,new{ok=false},timeout.Token); return; }
    var result=await invoke(new(){Action=path=="/v1/connect"?"connect":"launch",Ticket=request.Ticket}).WaitAsync(timeout.Token);
    await Reply(stream,result.Ok?200:409,origin,new{ok=result.Ok},timeout.Token);
   } catch(Exception e) when(e is IOException or SocketException or JsonException or OperationCanceledException or InvalidOperationException or UnauthorizedAccessException) { }
  }
 }
 private static async Task Reply(NetworkStream stream,int status,string origin,object? body,CancellationToken ct) {
  var bytes=body is null?[]:JsonSerializer.SerializeToUtf8Bytes(body,Wire.Json);
  var header=$"HTTP/1.1 {status} {(status==200?"OK":status==204?"No Content":"Unavailable")}\r\nContent-Type: application/json\r\nContent-Length: {bytes.Length}\r\nConnection: close\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: {origin}\r\nVary: Origin\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\nAccess-Control-Allow-Private-Network: true\r\n\r\n";
  await stream.WriteAsync(Encoding.ASCII.GetBytes(header),ct); if(bytes.Length!=0) await stream.WriteAsync(bytes,ct); await stream.FlushAsync(ct);
 }
 public void Dispose() { stop.Cancel(); listener?.Stop(); stop.Dispose(); }
 private sealed class TicketRequest { public string? Ticket{get;set;} public string? Challenge{get;set;} }
}
