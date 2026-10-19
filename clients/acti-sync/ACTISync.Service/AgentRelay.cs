using ACTISync.Core;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;

namespace ACTISync.Service;

public sealed partial class SyncEngine {
 private readonly ConcurrentDictionary<string,AgentState> agents=[];
 private readonly ConcurrentDictionary<string,DateTimeOffset> pulses=[];
 private readonly ConcurrentDictionary<string,DateTimeOffset> agentContacts=[];
 internal Func<DateTimeOffset> Clock { get; set; }=()=>DateTimeOffset.UtcNow;
 private string AgentPath(string id)=>Path.Combine(root,"agents",Guid.Parse(id).ToString("D")+".json");
 private void PersistAgent(AgentState agent)=>DurableJson.Write(AgentPath(agent.ConnectionId),agent);
 private void InitializeAgents() {
  Security.DirectoryAcl(Path.Combine(root,"agents"));
  foreach(var path in Directory.EnumerateFiles(Path.Combine(root,"agents"),"*.json")) {
   try {
    var agent=DurableJson.Read<AgentState>(path);
    if(agent is null || !Guid.TryParse(agent.ConnectionId,out _) || !config.Matches(agent.ServerOrigin)) continue;
    foreach(var id in agent.Pending.Where(p=>p.Value.ExpiresAt<=Clock() && !agent.Started.Contains(p.Key)).Select(p=>p.Key).ToArray()) agent.Pending.Remove(id);
    // A started native operation has uncertain outcome after a crash. Never replay it.
    foreach(var id in agent.Started.ToArray()) Complete(agent,id,false);
    PersistAgent(agent); agents[agent.ConnectionId]=agent;
   } catch(Exception e) when(e is IOException or JsonException or ArgumentException or UnauthorizedAccessException) { }
  }
 }
 private void SaveAgent(string sid,AgentCredentials credentials) {
  if(!Guid.TryParse(credentials.ConnectionId,out _) || string.IsNullOrWhiteSpace(credentials.Token) || credentials.ExpiresAt<=DateTimeOffset.UtcNow) throw new IOException("Недопустимое подключение агента.");
  var next=new AgentState{ConnectionId=credentials.ConnectionId,ServerOrigin=config.Origin.AbsoluteUri,Sid=sid,ProtectedToken=Security.Protect(credentials.Token),ExpiresAt=credentials.ExpiresAt};
  if(agents.TryGetValue(next.ConnectionId,out var prior)) lock(prior) {
   if(prior.Sid!=sid || prior.ServerOrigin!=next.ServerOrigin) throw new UnauthorizedAccessException();
   prior.ProtectedToken=next.ProtectedToken; prior.ExpiresAt=next.ExpiresAt;
   PersistAgent(prior);
  } else { PersistAgent(next); agents[next.ConnectionId]=next; }
 }
 private async Task<IpcResponse> Connect(string sid,string? ticket,CancellationToken ct) {
  if(ticket is null || !System.Text.RegularExpressions.Regex.IsMatch(ticket,@"\A[A-Za-z0-9_-]{43}\z")) return new(){Error="Недопустимый одноразовый билет."};
  using var response=await http.PostAsJsonAsync(config.Api("agent/exchange"),new{ticket,client_version=Wire.Version},Wire.Json,ct);
  await Ensure(response); SaveAgent(sid,await response.Content.ReadFromJsonAsync<AgentCredentials>(Wire.Json,ct) ?? throw new IOException());
  return new(){Ok=true};
 }
 private IpcResponse Pulse(string sid) {
  pulses[sid]=Clock();
  var commands=new List<AgentCommand>();
  if(!NativeDispatchPaused()) foreach(var agent in agents.Values.Where(a=>a.Sid==sid && config.Matches(a.ServerOrigin))) lock(agent) commands.AddRange(agent.Pending.Values.Where(c=>c.ExpiresAt>Clock() && !agent.Started.Contains(c.Id) && !agent.Completed.ContainsKey(c.Id)));
  return new(){Ok=true,Commands=commands,AgentOnline=agents.Values.Any(a=>a.Sid==sid && config.Matches(a.ServerOrigin) && agentContacts.TryGetValue(a.ConnectionId,out var seen) && Clock()>=seen && Clock()-seen<=TimeSpan.FromSeconds(35))};
 }
 private AgentState? FindCommand(string sid,string id,string? sessionId) {
  if(sessionId is null || !sessions.TryGetValue(sessionId,out var session) || session.Sid!=sid || session.ConnectionId is null || !agents.TryGetValue(session.ConnectionId,out var agent) || agent.Sid!=sid || !config.Matches(agent.ServerOrigin)) return null;
  lock(agent) return agent.Completed.ContainsKey(id) || agent.Pending.TryGetValue(id,out var command) && command.SessionId==sessionId && command.ExpiresAt>Clock() ? agent : null;
 }
 private void Complete(AgentState agent,string id,bool ok) {
  var result=new CommandResult{Id=id,Ok=ok,Error=ok ? null : "Действие не выполнено. Проверьте состояние документа в ACTI."};
  agent.Completed[id]=result; agent.Results.RemoveAll(r=>r.Id==id); agent.Results.Add(result); agent.Pending.Remove(id); agent.Started.Remove(id);
  while(agent.Completed.Count>512) agent.Completed.Remove(agent.Completed.Keys.First());
  PersistAgent(agent);
 }
 // Independent of document network/storage work: a user pulse cannot wait behind
 // a large snapshot/upload, and the service alone can never claim user presence.
 public async Task AgentTick(CancellationToken ct) {
  foreach(var agent in agents.Values) {
   if(!config.Matches(agent.ServerOrigin) || !pulses.TryGetValue(agent.Sid,out var pulse) || !AgentPresence.IsFresh(pulse,Clock())) continue;
   try {
    string protectedToken; DateTimeOffset expires; List<CommandResult> results;
    lock(agent) { protectedToken=agent.ProtectedToken; expires=agent.ExpiresAt; results=agent.Results.ToList(); }
    if(expires<DateTimeOffset.UtcNow.AddMinutes(3)) {
     using var renew=await SendAgent(agent,protectedToken,"renew",new{},ct); await Ensure(renew);
     var rotated=await renew.Content.ReadFromJsonAsync<AgentCredentials>(Wire.Json,ct) ?? throw new IOException();
     if(rotated.ConnectionId!=agent.ConnectionId || string.IsNullOrWhiteSpace(rotated.Token)) throw new IOException();
     lock(agent) { if(agent.ProtectedToken!=protectedToken) continue; agent.ProtectedToken=Security.Protect(rotated.Token); agent.ExpiresAt=rotated.ExpiresAt; protectedToken=agent.ProtectedToken; PersistAgent(agent); }
    }
    if(!pulses.TryGetValue(agent.Sid,out pulse) || !AgentPresence.IsFresh(pulse,Clock())) continue;
    using var response=await SendAgent(agent,protectedToken,"heartbeat",new{command_results=results,client_version=Wire.Version},ct); await Ensure(response);
    var heartbeat=await response.Content.ReadFromJsonAsync<AgentHeartbeat>(Wire.Json,ct) ?? throw new IOException();
    agentContacts[agent.ConnectionId]=Clock();
    lock(agent) {
     // Connect/launch may rotate this credential while HTTP is in flight.
     if(!agents.TryGetValue(agent.ConnectionId,out var current) || !ReferenceEquals(current,agent)) continue;
     agent.Results.RemoveAll(r=>results.Any(sent=>sent.Id==r.Id));
     var valid=heartbeat.Commands.Where(c=>c.ExpiresAt>Clock() && Guid.TryParse(c.Id,out _) && Guid.TryParse(c.SessionId,out _) && c.Action is "open" or "retry" or "finish" or "reveal").ToDictionary(c=>c.Id);
     foreach(var id in agent.Pending.Keys.Where(id=>!valid.ContainsKey(id) && !agent.Started.Contains(id)).ToArray()) agent.Pending.Remove(id);
     foreach(var command in valid.Values) {
      if(agent.Completed.TryGetValue(command.Id,out var done)) { if(!agent.Results.Any(r=>r.Id==done.Id)) agent.Results.Add(done); }
      else agent.Pending[command.Id]=command;
     }
     PersistAgent(agent);
    }
   } catch(Exception e) when(e is HttpRequestException or IOException or UnauthorizedAccessException or CryptographicException or TaskCanceledException or JsonException or InvalidOperationException) {
    if(ct.IsCancellationRequested) return;
    // Retain encrypted credentials and command receipts for reconnect/recovery.
   }
  }
 }
 private async Task<HttpResponseMessage> SendAgent(AgentState agent,string protectedToken,string operation,object body,CancellationToken ct) {
  if(!config.Matches(agent.ServerOrigin)) throw new UnauthorizedAccessException();
  using var request=new HttpRequestMessage(HttpMethod.Post,config.Api($"agents/{Guid.Parse(agent.ConnectionId):D}/{operation}")){Content=JsonContent.Create(body,options:Wire.Json)};
  request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",Security.Unprotect(protectedToken));
  return await http.SendAsync(request,ct);
 }
}
