using ACTISync.Core;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;

namespace ACTISync.Service;

public sealed partial class SyncEngine : IDisposable {
 private readonly string root;
 private readonly ConcurrentDictionary<string, SessionState> sessions = [];
 private readonly ConcurrentDictionary<string,SessionView> views=[];
 private readonly ConcurrentDictionary<string,SessionState> heartbeatStates=[];
 private readonly ConcurrentDictionary<string,SemaphoreSlim> sessionGates=[];
 private readonly ConcurrentDictionary<string,SemaphoreSlim> launchGates=[];
 private readonly SemaphoreSlim transfers=new(4,4);
 private readonly object quotaGate=new();
 private readonly Dictionary<string,long> reservedBytes=[];
 private SemaphoreSlim SessionGate(string id)=>sessionGates.GetOrAdd(id,_=>new(1,1));
 internal TimeSpan RequestTimeout {get;set;}=TimeSpan.FromSeconds(10);
 private readonly ConcurrentBag<FileSystemWatcher> watchers = [];
 private readonly HttpClient http;
 private ClientConfig config = null!;
 internal Func<bool> NativeDispatchPaused {get;set;}=()=>NativeDispatchMaintenance.Active;
 public SyncEngine() { root=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"ACTI","Sync"); http=new(new HttpClientHandler { AllowAutoRedirect=false }) { Timeout=TimeSpan.FromSeconds(10),MaxResponseContentBufferSize=Wire.MaxFileSize+65536 }; }
 internal SyncEngine(string testRoot,ClientConfig testConfig,HttpClient testHttp) { root=testRoot; config=testConfig; http=testHttp; http.MaxResponseContentBufferSize=Wire.MaxFileSize+65536; }
 private string StatePath(string id) => Path.Combine(root, "state", Guid.Parse(id).ToString("D") + ".json");
 private string Snapshots(SessionState s) => Path.Combine(root, "snapshots", Guid.Parse(s.SessionId).ToString("D"));
 private void Persist(SessionState s) {
  if(telemetryCapabilities.TryGetValue((s.SessionId,s.ServerOrigin),out var telemetryEnabled))s.SupportsTelemetry=telemetryEnabled;
  DurableJson.Write(StatePath(s.SessionId),s);
  var view=SessionView.From(s);
  if(s.RecoveryCommandId!=null && s.RecoverySha256!=null) {view.RecoveryPath=RecoveryPath(s);view.RecoveryCommandId=s.RecoveryCommandId;view.RecoverySha256=s.RecoverySha256;}
  views[s.SessionId]=view;
  heartbeatStates[s.SessionId]=new(){SupportsTelemetry=s.SupportsTelemetry,SessionId=s.SessionId,Sid=s.Sid,ServerOrigin=s.ServerOrigin,ProtectedToken=s.ProtectedToken,Status=s.Status,Opened=s.Opened,DocumentOpen=s.DocumentOpen,ErrorCode=s.ErrorCode,Finished=s.Finished,Pending=s.Pending is null ? null : new(),NextPending=s.NextPending is null ? null : new()};
 }
 public void Initialize() {
  config ??= ClientConfig.Load(); Security.DirectoryAcl(root, traverse:true);
  Security.DirectoryAcl(Path.Combine(root,"recovery"),traverse:true);
  Security.DirectoryAcl(Path.Combine(root, "state")); Security.DirectoryAcl(Path.Combine(root, "snapshots")); Security.DirectoryAcl(Path.Combine(root, "work"), traverse:true);
  InitializeAgents();
  foreach (var path in Directory.EnumerateFiles(Path.Combine(root,"state"), "*.json")) {
   try { var s = DurableJson.Read<SessionState>(path); if (s is null || !Guid.TryParse(s.SessionId, out _) || !s.WorkingPath.StartsWith(Path.Combine(root,"work") + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) continue; SafeFiles.NoReparse(s.WorkingPath); Security.DirectoryAcl(Path.GetDirectoryName(s.WorkingPath)!,s.Sid); if(!config.Matches(s.ServerOrigin)) { s.Status="reauth"; s.Error="Сервер ACTI в настройке установки изменён. Старая очередь изолирована; сохраните локальную копию и обратитесь к администратору."; Persist(s); } sessions[s.SessionId]=s; Persist(s); Watch(s); }
   catch (Exception e) when (e is IOException or JsonException or ArgumentException or UnauthorizedAccessException) { /* Preserve corrupt state and workspaces for recovery; never erase them. */ }
  }
  // Recover the crash window between durable server ACK/local Finished and the
  // separate durable command receipt. Never reissue HTTP for this window.
  foreach(var s in sessions.Values.Where(s=>s.Finished && s.RecoveryCommandId!=null && s.RecoverySha256!=null && s.ConnectionId!=null)) {
   if(agents.TryGetValue(s.ConnectionId!,out var owner)) lock(owner) if(owner.Pending.TryGetValue(s.RecoveryCommandId!,out var command) && command.Action=="recover" && command.SessionId==s.SessionId) Complete(owner,command.Id,true);
  }
 }
 private void Watch(SessionState s) {
  if (!Directory.Exists(Path.GetDirectoryName(s.WorkingPath))) return;
  var w = new FileSystemWatcher(Path.GetDirectoryName(s.WorkingPath)!) { NotifyFilter=NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size, EnableRaisingEvents=true };
  // Watcher is a latency hint. Periodic content hashing remains authoritative after lost events.
  w.Changed += (_,_) => s.Dirty=true; w.Created += (_,_) => s.Dirty=true; w.Renamed += (_,_) => s.Dirty=true;
  watchers.Add(w);
 }
 public async Task<IpcResponse> Handle(string sid, IpcRequest request, CancellationToken ct) {
  if(request.Action=="presence") return new(){Ok=true,AgentOnline=AgentOnline(sid)};
  if(request.Action=="pulse") return Pulse(sid);
  if(NativeDispatchPaused() && request.Action is "launch" or "command_start")return new(){Error="Агент обновляется. Повторите действие позднее."};
  if(request.Action=="list") return new(){Ok=true,Sessions=sessions.Values.Where(s=>s.Sid==sid && !s.Finished).Select(s=>views[s.SessionId]).ToList()};
  var operationGate=request.Action is "launch" or "connect" ? launchGates.GetOrAdd(sid,_=>new(1,1)) : request.SessionId is not null && sessions.TryGetValue(request.SessionId,out var requestedSession) && requestedSession.Sid==sid ? SessionGate(request.SessionId) : null;
  if(operationGate is null) return new(){Error="Сеанс не найден."};
  await operationGate.WaitAsync(ct);
  try {
   if(NativeDispatchPaused() && request.Action is "launch" or "command_start")return new(){Error="Агент обновляется. Повторите действие позднее."};
   AgentState? owner=null;
   if(request.CommandId!=null) {
    owner=FindCommand(sid,request.CommandId,request.SessionId);
    if(owner is null) return new(){Error="Команда не принадлежит этому сеансу пользователя."};
    lock(owner) {
     if(owner.Completed.TryGetValue(request.CommandId,out var done)) {
      var completedSession=sessions[request.SessionId!];
      if(request.Action=="recovery_revealed" && done.Ok && completedSession.Finished && completedSession.RecoveryCommandId==request.CommandId) {completedSession.RecoveryRevealPending=false;Persist(completedSession);}
      return new(){Ok=done.Ok,Error=done.Error,Session=views[completedSession.SessionId]};
     }
     var command=owner.Pending[request.CommandId];
     if(request.Action=="command_result") {
      if(command.Action is not ("open" or "reveal") || !owner.Started.Contains(command.Id)) return new(){Error="Действие не подтверждено службой."};
      Complete(owner,request.CommandId,request.Ok==true); return new(){Ok=true};
     }
     if(request.Action=="command_start") {
      if(sessions[command.SessionId].Finished) { Complete(owner,command.Id,false); return new(){Error="Сеанс завершён; откройте новую редакцию из ACTI."}; }
      if(command.Action is not ("open" or "reveal") || !owner.Started.Add(command.Id)) return new(){Error="Команда уже принята."};
      PersistAgent(owner); return new(){Ok=true,Session=SessionView.From(sessions[command.SessionId])};
     }
     if(request.Action!=command.Action || command.Action is not ("retry" or "finish" or "recover") || !owner.Started.Add(command.Id)) return new(){Error="Недопустимая команда."};
     PersistAgent(owner);
    }
   }
   var response=await HandleLocked(sid,request,ct);
   if(owner!=null && (response.Ok || request.SessionId is null || !sessions[request.SessionId].RecoveryPending)) lock(owner) Complete(owner,request.CommandId!,response.Ok,request.SessionId is null ? null : sessions[request.SessionId].ErrorCode);
   return response;
  } finally { operationGate.Release(); }
 }
 private async Task<IpcResponse> HandleLocked(string sid,IpcRequest request,CancellationToken ct) {
  try {
   if(request.Action=="connect") return await Connect(sid,request.Ticket,ct);
   if (request.Action == "launch") {
    if (request.Ticket is null || !System.Text.RegularExpressions.Regex.IsMatch(request.Ticket, @"\A[A-Za-z0-9_-]{43}\z")) return new() { Error="Недопустимый одноразовый билет." };
    if (sessions.Values.Count(s=>s.Sid==sid && !s.Finished) >= 20) return new() { Error="Открыто слишком много файлов. Завершите ненужные сеансы." };
    try {
     using var exchange=new HttpRequestMessage(HttpMethod.Post,config.Api("exchange")){Content=JsonContent.Create(new { ticket=request.Ticket, client_version=Wire.Version },options:Wire.Json)};
     using var result=await SendBounded(exchange,ct);
     await Ensure(result);
     var server = await result.Content.ReadFromJsonAsync<ServerSession>(Wire.Json, ct) ?? throw new IOException();
     if(!SafeFiles.WordSourceFormat(server.SourceFormat)) throw new IOException("Формат Word не поддерживается.");
     if (!Guid.TryParse(server.SessionId,out _) || server.Token is null) throw new IOException("Недопустимый ответ сервера.");
     if(server.Agent is null) throw new IOException("Сервер не подтвердил подключение агента.");
     SaveAgent(sid,server.Agent);
     var prior = sessions.Values.FirstOrDefault(s=>s.Sid==sid && s.FileId==server.FileId && config.Matches(s.ServerOrigin) && !s.Finished);
     var registrationGate=SessionGate(server.SessionId);
     await registrationGate.WaitAsync(ct);
     SemaphoreSlim? priorGate=prior!=null && prior.SessionId!=server.SessionId ? SessionGate(prior.SessionId) : null;
     try {
     if(priorGate!=null) await priorGate.WaitAsync(ct);
     try {
     if(sessions.TryGetValue(server.SessionId,out var registered) && registered.Sid!=sid) throw new UnauthorizedAccessException();
     if(prior?.RecoveryPending==true) throw new IOException("Сначала дождитесь подтверждения восстановления локальной копии.");
     var s = new SessionState { SourceFormat=server.SourceFormat,ConnectionId=server.Agent.ConnectionId,ServerOrigin=config.Origin.AbsoluteUri, SessionId=server.SessionId, FileId=server.FileId, Sid=sid, FileName=SafeFiles.FileName(server.Filename), ProtectedToken=Security.Protect(server.Token), AckHash=server.Sha256, Revision=server.Revision, ExpiresAt=server.ExpiresAt, LeaseExpiresAt=server.LeaseExpiresAt };
     LearnCapabilities(s,server);
     Security.DirectoryAcl(Path.Combine(root,"work",sid), traverse:true);
     var work = Path.Combine(root,"work",sid,s.SessionId); Security.DirectoryAcl(work,sid);
     s.WorkingPath=Path.Combine(work,s.FileName); Security.DirectoryAcl(Snapshots(s));
     if(s.SourceFormat!="docx") { s.ConversionSourcePath=Path.Combine(work,"original."+s.SourceFormat); s.ConversionSourceSha256=server.Sha256; }
     if (prior is not null) {
      s.WorkingPath=prior.WorkingPath; s.Pending=prior.Pending; s.NextPending=prior.NextPending; s.Opened=prior.Opened; s.DocumentOpen=prior.DocumentOpen;
      if(prior.ConversionSourcePath!=null) { s.ConversionSourcePath=prior.ConversionSourcePath; s.ConversionSourceSha256=prior.ConversionSourceSha256; }
      s.Status = prior.AckHash==server.Sha256 ? "editing" : "conflict";
      if(s.Status=="conflict") s.Error="На сервере уже другая редакция. Локальная копия сохранена; сохраните её отдельно для разрешения конфликта.";
      if(s.Pending!=null && s.Status!="conflict") s.Pending.ExpectedRevision=server.Revision;
      prior.Finished=true; prior.Status="finished"; Persist(prior);
     }
     Persist(s); sessions[s.SessionId]=s; Watch(s);
     if (!File.Exists(s.WorkingPath)) await Download(s,ct);
     return new() { Ok=true, Session=SessionView.From(s) };
     } finally { priorGate?.Release(); }
     } finally { registrationGate.Release(); }
    } catch (Exception e) when (e is HttpRequestException or IOException or TaskCanceledException or ArgumentException or UnauthorizedAccessException or CryptographicException or JsonException or InvalidOperationException) { return new() { Error=UserError(e) }; }
   }
   if(request.Action=="list") return new() { Ok=true, Sessions=sessions.Values.Where(s=>s.Sid==sid && !s.Finished).Select(SessionView.From).ToList() };
   if(request.SessionId is null || !sessions.TryGetValue(request.SessionId,out var session) || session.Sid!=sid) return new() { Error="Сеанс не найден." };
   switch(request.Action) {
    case "recover": return await Recover(session,request.CommandId,ct);
    case "opened": if(!OwnerFilePresent(session)) return new(){Error="Открытие в Word ещё не подтверждено."}; session.Opened=true; session.DocumentOpen=true; session.PresenceDirty=true; session.RetryAt=DateTimeOffset.MinValue; if(!session.Blocked) session.Status="editing"; break;
    case "word_missing": session.ErrorCode="word_missing";session.Status="error"; session.PresenceDirty=true; session.Error="Microsoft Word не найден. Локальная копия сохранена."; break;
    case "open_failed": session.Status="error"; session.PresenceDirty=true; session.Error="Открытие в Word не подтверждено. Проверьте Word и повторите действие в ACTI; локальная копия сохранена."; break;
    case "retry": if(session.Status is not ("conflict" or "reauth")) session.RetryAt=DateTimeOffset.MinValue; if(session.Status is "offline" or "error") { session.Status="local_saved"; session.Failures=0; session.RetryAt=DateTimeOffset.MinValue; session.Error=null; } break;
    case "finish":
     if(Directory.EnumerateFiles(Path.GetDirectoryName(session.WorkingPath)!,"~$*.docx").Any() || !SafeFiles.ValidDocx(session.WorkingPath)) return new() { Error="Сначала закройте Word и дождитесь завершения сохранения DOCX." };
     var stableHash=await Capture(session,ct);
     if(stableHash is null || stableHash!=session.AckHash || session.Blocked || session.Pending!=null || session.NextPending!=null) return new() { Error="Не подтверждено сохранение текущего DOCX. Сеанс оставлен активным; устраните ошибку или сохраните локальную копию." };
     // Keep the verified current object locked against writes/replacement through
     // the finish commit; a save racing with finish must not become invisible.
     using(var current=SafeFiles.OpenRead(session.WorkingPath)) {
      if(Convert.ToHexStringLower(await SHA256.HashDataAsync(current,ct))!=session.AckHash) return new() { Error="DOCX изменился во время завершения. Дождитесь синхронизации или сохраните локальную копию." };
      using(var response=await Send(session,HttpMethod.Post,"finish",JsonContent.Create(new {},options:Wire.Json),ct)) await Ensure(response);
     }
     session.Finished=true; session.Status="finished"; break;
    default: return new() { Error="Неизвестное действие." };
   }
   Persist(session); return new() { Ok=true, Session=SessionView.From(session) };
  } catch(Exception e) when(e is HttpRequestException or IOException or TaskCanceledException or UnauthorizedAccessException or CryptographicException or JsonException or InvalidOperationException) { return new() { Error=UserError(e) }; }
 }
 private static bool OwnerFilePresent(SessionState s) { SafeFiles.NoReparse(Path.GetDirectoryName(s.WorkingPath)!); return Directory.EnumerateFiles(Path.GetDirectoryName(s.WorkingPath)!,"~$*.docx").Any(); }
 private async Task Download(SessionState s,CancellationToken ct) {
  // If a working copy is missing, durable unsent bytes take precedence over
  // any server download or original conversion input.
  if(!File.Exists(s.WorkingPath) && (s.NextPending ?? s.Pending) is { } queued) {
   SafeFiles.NoReparse(queued.Path); using var snapshot=SafeFiles.OpenRead(queued.Path);
   if(!SafeFiles.ValidDocx(queued.Path) || Convert.ToHexStringLower(await SHA256.HashDataAsync(snapshot,ct))!=queued.Hash) throw new IOException("Снимок очереди повреждён; исходные данные сохранены.");
   SafeFiles.NoReparse(s.WorkingPath);File.Copy(queued.Path,s.WorkingPath,false);return;
  }
  var converting=s.SourceFormat!="docx";
  var destination=converting ? s.ConversionSourcePath ?? throw new IOException() : s.WorkingPath;
  if(converting && File.Exists(destination)) {
   SafeFiles.NoReparse(destination); using var existing=SafeFiles.OpenRead(destination);
   if(!SafeFiles.WordConversionInput(destination,s.SourceFormat) || Convert.ToHexStringLower(await SHA256.HashDataAsync(existing,ct))!=s.ConversionSourceSha256) throw new IOException("Исходный файл изменён; локальные данные сохранены.");
   return;
  }
  using var response=await Send(s,HttpMethod.Get,"download",null,ct); await Ensure(response);
  var temporary=Path.Combine(Snapshots(s),"initial.docx");
  using(var input=await response.Content.ReadAsStreamAsync(ct)) using(var output=new FileStream(temporary,FileMode.Create,FileAccess.Write,FileShare.None,81920,FileOptions.WriteThrough)) {
   var buffer=new byte[81920]; long total=0; int read; while((read=await input.ReadAsync(buffer,ct))!=0) { total+=read; if(total>Wire.MaxFileSize) throw new IOException("Файл больше 25 МиБ."); await output.WriteAsync(buffer.AsMemory(0,read),ct); } output.Flush(true);
  }
  if(converting ? !SafeFiles.WordConversionInput(temporary,s.SourceFormat) : !SafeFiles.ValidDocx(temporary)) throw new IOException("Сервер вернул неподдерживаемый или неполный файл Word.");
  using(var f=File.OpenRead(temporary)) if(Convert.ToHexStringLower(await SHA256.HashDataAsync(f,ct))!=s.AckHash) throw new IOException("Контрольная сумма DOCX не совпадает.");
  SafeFiles.NoReparse(destination);
  if(!File.Exists(destination)) File.Copy(temporary,destination,false);
  File.Delete(temporary); s.Status="opening"; Persist(s);
 }
 private async Task<string?> Capture(SessionState s,CancellationToken ct) {
  if(!File.Exists(s.WorkingPath)) return null;
  if(new FileInfo(s.WorkingPath).Length>Wire.MaxFileSize) { s.ErrorCode="quota";s.Status="error"; s.Error="Размер превышает 25 МиБ. Локальный файл сохранён."; Persist(s); return null; }
  var reservation=new FileInfo(s.WorkingPath).Length;
  if(!ReserveBytes(s,reservation)) {QuotaError(s);return null;}
  try {
  // Two coalesced durable pending snapshots limit storage while preserving an uncertain in-flight request.
  var snapshot=await SafeFiles.SnapshotAsync(s.WorkingPath,Snapshots(s),ct); if(snapshot is null) return null;
  var newest=s.NextPending?.Hash ?? s.Pending?.Hash ?? s.AckHash;
  if(snapshot.Hash==newest) { File.Delete(snapshot.Path); return snapshot.Hash; }
  snapshot.ExpectedRevision=s.Revision;
  Snapshot? old=null;
  if(s.Pending is null) s.Pending=snapshot; else { old=s.NextPending; s.NextPending=snapshot; }
  if(!s.Blocked) s.Status="local_saved";
  Persist(s); if(old!=null) File.Delete(old.Path); return snapshot.Hash;
  } finally {ReleaseBytes(s,reservation);}
 }
 public Task Tick(CancellationToken ct)=>Task.WhenAll(sessions.Values.Where(s=>!s.Finished).Select(s=>TickSession(s,ct)));
 private async Task TickSession(SessionState s,CancellationToken ct) {
  var operationGate=SessionGate(s.SessionId);
  if(!await operationGate.WaitAsync(0,ct)) return;
  try {
     if(s.Finished) return;
     if(s.RecoveryPending) {
      var recovered=await Recover(s,s.RecoveryCommandId,ct);
      if(recovered.Ok && s.ConnectionId!=null && agents.TryGetValue(s.ConnectionId,out var recoveryOwner)) lock(recoveryOwner) Complete(recoveryOwner,s.RecoveryCommandId!,true);
      return;
     }
     if(s.Opened) {
      var documentOpen=OwnerFilePresent(s);
      if(s.DocumentOpen!=documentOpen) { s.DocumentOpen=documentOpen; s.PresenceDirty=true; s.RetryAt=DateTimeOffset.MinValue; Persist(s); }
     }
     // Word close is independent of upload ACK, including conflicts and queued
     // unsent changes. A failed report is retained durably for the next poll.
     if(s.PresenceDirty) {
      try {
       using var presence=await SendHeartbeat(s,ct,ownsSession:true);
       await Ensure(presence); LearnCapabilities(s,await presence.Content.ReadFromJsonAsync<ServerSession>(Wire.Json,ct));s.PresenceDirty=false; Persist(s);
      } catch(Exception e) when(e is HttpRequestException or IOException or TaskCanceledException) { if(ct.IsCancellationRequested) throw; }
     }
     string? observedHash;
     try { observedHash=await Capture(s,ct); }
     catch(Exception local) when(local is IOException or UnauthorizedAccessException) {
      // A Word byte-range lock/replace is a local retry, not a failed ACTI
      // connection. Continue the lease heartbeat while waiting for safe bytes.
      observedHash=null;
     }
     if(observedHash is null && !s.Blocked) { s.Status=s.Opened ? "editing" : "opening"; s.Error=null; }
     if(s.Dirty && !s.Blocked) { s.Dirty=false; s.RetryAt=DateTimeOffset.MinValue; }
     if(await FinishClosed(s,observedHash,ct)) return;
     if(s.RetryAt>DateTimeOffset.UtcNow || s.RecoveryPending) return;
     if(s.Status is not ("reauth" or "error") && !await ResumeLease(s,ct)) return;
     if(s.Blocked) return;
     if(!File.Exists(s.WorkingPath)) await Download(s,ct);
     if(s.ExpiresAt < DateTimeOffset.UtcNow.AddMinutes(3)) {
      using var renew=await Send(s,HttpMethod.Post,"renew",JsonContent.Create(new{},options:Wire.Json),ct); await Ensure(renew);
      var token=await renew.Content.ReadFromJsonAsync<ServerSession>(Wire.Json,ct) ?? throw new IOException();
      if(token.Token is null) throw new IOException(); LearnCapabilities(s,token);s.ProtectedToken=Security.Protect(token.Token); s.ExpiresAt=token.ExpiresAt; s.LeaseExpiresAt=token.LeaseExpiresAt; Persist(s);
     }
     if(s.Pending is { } pending) {
      s.Status="syncing"; s.ErrorCode=null;Persist(s);
      using var input=File.OpenRead(pending.Path); using var content=new StreamContent(input); content.Headers.ContentType=new MediaTypeHeaderValue("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
      using var request=Request(s,HttpMethod.Put,"save",content); request.Headers.TryAddWithoutValidation("If-Match",$"\"{pending.ExpectedRevision}\""); request.Headers.TryAddWithoutValidation("Idempotency-Key",pending.Key); request.Headers.TryAddWithoutValidation("X-Content-SHA256",pending.Hash);
      using var response=await SendBounded(request,ct,transfer:true); await Ensure(response);
      var ack=await response.Content.ReadFromJsonAsync<ServerSession>(Wire.Json,ct) ?? throw new IOException();
      if(ack.Sha256!=pending.Hash || ack.Revision < s.Revision) throw new IOException("Сервер не подтвердил содержимое.");
      LearnCapabilities(s,ack);
      s.SourceFormat="docx"; s.Revision=ack.Revision; s.AckHash=ack.Sha256; s.LastAck=ack.LastSavedAt ?? DateTimeOffset.UtcNow; s.LeaseExpiresAt=ack.LeaseExpiresAt;
      s.Pending=s.NextPending; s.NextPending=null; if(s.Pending!=null) s.Pending.ExpectedRevision=s.Revision;
      s.Status=s.Pending!=null ? "local_saved" : observedHash==pending.Hash ? "saved" : "editing"; s.Failures=0; s.Error=null;s.ErrorCode=null; Persist(s); content.Dispose(); input.Dispose(); File.Delete(pending.Path);
      // Word can save again while the immutable pending request is in flight.
      // Reobserve after ACK before advertising a fully drained/saved session.
      observedHash=await Capture(s,ct);
     }
     if(await FinishClosed(s,observedHash,ct)) return;
     using var heartbeat=await SendHeartbeat(s,ct,ownsSession:true); await Ensure(heartbeat);
     var alive=await heartbeat.Content.ReadFromJsonAsync<ServerSession>(Wire.Json,ct); if(alive!=null) {LearnCapabilities(s,alive);s.LeaseExpiresAt=alive.LeaseExpiresAt;}
     s.RetryAt=s.Pending!=null ? DateTimeOffset.MinValue : DateTimeOffset.UtcNow.AddSeconds(20); Persist(s);
    } catch(Exception e) when(e is HttpRequestException or IOException or UnauthorizedAccessException or CryptographicException or TaskCanceledException or JsonException or InvalidOperationException) {
     if(ct.IsCancellationRequested) return;
     if(e is CryptographicException) s.Status="reauth";
     else if(e is ApiException api && api.Status is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden) s.Status="reauth";
     else if(e is ApiException conflict && conflict.Status is HttpStatusCode.Conflict or HttpStatusCode.PreconditionFailed) s.Status="conflict";
     else if(e is ApiException fatal && (int)fatal.Status is >=400 and <500 && fatal.Status!=HttpStatusCode.RequestTimeout && (int)fatal.Status!=429) s.Status="error";
     else s.Status="offline";
     s.ErrorCode=s.Status switch {"reauth"=>"reauth","conflict"=>"conflict","error"=>"server_rejected",_=>"network"};
     s.Error=UserError(e); s.Failures=Math.Min(s.Failures+1,10); s.RetryAt=DateTimeOffset.UtcNow.AddSeconds(Math.Min(60,Math.Pow(2,s.Failures)));
     Persist(s);
    } finally { operationGate.Release(); }
 }
 private async Task<bool> FinishClosed(SessionState session,string? observedHash,CancellationToken ct) {
  if(!session.Opened || session.DocumentOpen!=false || session.Blocked || session.Pending!=null || session.NextPending!=null || observedHash is null || observedHash!=session.AckHash) return false;
  if(session.ConnectionId!=null && agents.TryGetValue(session.ConnectionId,out var owner)) lock(owner) {
   if(owner.Pending.Values.Any(command=>command.SessionId==session.SessionId && command.Action=="open" && command.ExpiresAt>Clock() && !owner.Completed.ContainsKey(command.Id))) return false;
  }
  // Reuse the restrictive, rehashed manual-finish path under the existing gate.
  // Word close alone never discards a pending/unacknowledged local save.
  var result=await HandleLocked(session.Sid,new(){Action="finish",SessionId=session.SessionId},ct);
  return result.Ok;
 }
 private HttpRequestMessage Request(SessionState s,HttpMethod method,string operation,HttpContent? content) { if(!config.Matches(s.ServerOrigin)) throw new UnauthorizedAccessException("Сервер сохранённого сеанса не совпадает с настройкой установки."); var request=new HttpRequestMessage(method,config.Api($"sessions/{Guid.Parse(s.SessionId):D}/{operation}")) { Content=content }; request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",Security.Unprotect(s.ProtectedToken)); return request; }
 private async Task<HttpResponseMessage> Send(SessionState s,HttpMethod method,string operation,HttpContent? content,CancellationToken ct) { using var request=Request(s,method,operation,content); return await SendBounded(request,ct,transfer:operation=="download"); }
 private static async Task Ensure(HttpResponseMessage response) { if(response.IsSuccessStatusCode) return; await Task.CompletedTask; throw new ApiException(response.StatusCode); }
 private static string UserError(Exception e) => e is ApiException api ? api.Status switch { HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden => "Доступ истёк или отозван. Войдите в ACTI и снова нажмите «Редактировать в Word». Локальная копия сохранена.", HttpStatusCode.Conflict or HttpStatusCode.PreconditionFailed => "Конфликт редакции или истёкшая блокировка. Локальная копия сохранена; требуется повторный вход или сохранение копии.", HttpStatusCode.RequestEntityTooLarge => "Размер превышает 25 МиБ. Локальная копия сохранена.", _ => "ACTI отклонил запрос ("+(int)api.Status+"). Локальная копия сохранена." } : "Нет связи с ACTI или файл занят. Локальная копия и очередь сохранены; повтор будет выполнен автоматически.";
 public void Dispose() { foreach(var watcher in watchers) watcher.Dispose(); foreach(var semaphore in sessionGates.Values.Concat(launchGates.Values).Concat(agentGates.Values)) semaphore.Dispose(); transfers.Dispose(); http.Dispose(); }
 private sealed class ApiException(HttpStatusCode status) : HttpRequestException { public HttpStatusCode Status { get; }=status; }
}
