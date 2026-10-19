using ACTISync.Core;
using ACTISync.User;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

internal static class BrowserBridgeChecks {
 private static void Check(bool valid,string message) {if(!valid)throw new Exception(message);Console.WriteLine("PASS "+message);}
 internal static async Task Run() {
  using(var nativeDone=new ManualResetEventSlim()) {
   try {
    try {await WordSourceConversion.RunAsync(_=>nativeDone.Wait(),TimeSpan.FromMilliseconds(25));throw new Exception("native deadline ignored");}catch(TimeoutException){}
    Check(WordSourceConversion.IsBusy,"timed-out native conversion still blocks user-component update drain");
   } finally {nativeDone.Set();}
   Check(SpinWait.SpinUntil(()=>!WordSourceConversion.IsBusy,TimeSpan.FromSeconds(3)),"native conversion releases drain only after its STA actually exits");
  }
  var now=DateTimeOffset.UtcNow; var policy=new BridgePolicy(new Uri("https://example.com/"));
  Check(policy.AcceptOrigin("https://example.com") && !policy.AcceptOrigin("http://example.com") && !policy.AcceptOrigin("https://example.com.attacker.test") && !policy.AcceptOrigin(null),"bridge pins exact HTTPS Origin");
  var nonce=policy.Issue(now);Check(nonce.Length==43 && policy.Consume(nonce,new string('t',43),now) && !policy.Consume(nonce,new string('t',43),now),"bridge challenge is bounded and consumed exactly once");
  nonce=policy.Issue(now);Check(!policy.Consume(nonce,new string('t',43),now.AddSeconds(46)),"bridge refuses expired challenge");
  nonce=policy.Issue(now);Check(!policy.Consume(nonce,"bad",now) && policy.Consume(nonce,new string('t',43),now),"invalid ticket cannot consume or bypass a valid challenge");
  Check(!BrowserPeer.IsCurrentUser(new IPEndPoint(IPAddress.Parse("192.0.2.1"),10),43127),"bridge rejects non-loopback caller before identity lookup");
  var calls=0;var available=true;string? operation=null;
  using var bridge=new LoopbackBridge(new Uri("https://example.com/"),request=>{calls++;operation=request.Action;return Task.FromResult(new IpcResponse{Ok=true});},()=>available);
  Check(bridge.Port is >=43127 and <=43142,"bridge binds only its bounded loopback port range");
  using var client=new HttpClient(new HttpClientHandler{UseProxy=false,AllowAutoRedirect=false}){BaseAddress=new Uri($"http://127.0.0.1:{bridge.Port}"),Timeout=TimeSpan.FromSeconds(5)};
  client.DefaultRequestHeaders.Add("Origin","https://example.com");
  var status=await client.GetAsync("/v1/status");
  Check(status.StatusCode==HttpStatusCode.OK,"real TCP browser owner resolves to the same Windows SID/session");
  var payload=await status.Content.ReadFromJsonAsync<JsonElement>();nonce=payload.GetProperty("challenge").GetString()!;
  Task<HttpResponseMessage> Post() {var content=new ByteArrayContent(JsonSerializer.SerializeToUtf8Bytes(new{ticket=new string('t',43),challenge=nonce}));content.Headers.ContentType=new("application/json");return client.PostAsync("/v1/launch",content);}
  var post=await Post();
  Check(post.IsSuccessStatusCode && calls==1 && operation=="launch","bridge forwards only the validated one-use launch ticket");
  post=await Post();Check(post.StatusCode==HttpStatusCode.Forbidden && calls==1,"bridge rejects POST replay before service access");
  using var preflight=new HttpRequestMessage(HttpMethod.Options,"/v1/connect");preflight.Headers.Add("Access-Control-Request-Private-Network","true");
  var cors=await client.SendAsync(preflight);Check(cors.StatusCode==HttpStatusCode.NoContent && cors.Headers.GetValues("Access-Control-Allow-Origin").Single()=="https://example.com","private-network preflight is limited to pinned ACTI Origin");
  client.DefaultRequestHeaders.Remove("Origin");client.DefaultRequestHeaders.Add("Origin","https://attacker.test");
  try {await client.GetAsync("/v1/status");throw new Exception("foreign Origin accepted");}catch(HttpRequestException){Check(calls==1,"foreign Origin has no bridge response or side effects");}
  client.DefaultRequestHeaders.Remove("Origin");client.DefaultRequestHeaders.Add("Origin","https://example.com");available=false;
  Check((await client.GetAsync("/v1/status")).StatusCode==HttpStatusCode.ServiceUnavailable,"update drain disables new discovery/launch traffic");
  Console.WriteLine("ALL BROWSER BRIDGE CHECKS PASSED");
 }
}
