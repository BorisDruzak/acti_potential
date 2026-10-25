using ACTISync.Core;
using System.Net.Http.Json;
using System.Security.Cryptography;

namespace ACTISync.Service;

public sealed partial class SyncEngine {
 private string RecoveryPath(SessionState s)=>Path.Combine(root,"recovery",Guid.Parse(s.SessionId).ToString("D"),"recovered.docx");
 private async Task<IpcResponse> Recover(SessionState s,string? commandId,CancellationToken ct) {
  if(commandId is null || !Guid.TryParse(commandId,out _)) return new(){Error="Команда восстановления не подтверждена."};
  if(OwnerFilePresent(s)) { s.ErrorCode="file_busy";s.Error="Сначала закройте документ Word. Локальные изменения сохранены.";Persist(s);return new(){Error=s.Error}; }
  // No Word automation: deny writes/replacement while copying and committing.
  // Even a missing Word owner file cannot bypass an existing writable handle.
  FileStream current;
  try {current=SafeFiles.OpenRead(s.WorkingPath);}
  catch(IOException) {s.ErrorCode="file_busy";s.Error="Сначала закройте документ Word и дождитесь сохранения.";Persist(s);return new(){Error=s.Error};}
  using var currentHandle=current;
  if(current.Length>Wire.MaxFileSize || !SafeFiles.ValidDocx(s.WorkingPath)) {s.ErrorCode="local_io";Persist(s);return new(){Error="Не удалось проверить локальный DOCX. Все локальные файлы сохранены."};}
  var hash=Convert.ToHexStringLower(await SHA256.HashDataAsync(current,ct));
  var directory=Path.GetDirectoryName(RecoveryPath(s))!;
  SafeFiles.NoReparse(directory);Security.RecoveryDirectoryAcl(directory,s.Sid);
  var copy=RecoveryPath(s);
  if(s.RecoveryCommandId is null) {
   // Adopt a verified orphan copy if a crash occurred after publication but
   // before state persistence. Never overwrite a different preserved copy.
   if(!File.Exists(copy)) {
    if(!ReserveBytes(s,current.Length)) {QuotaError(s);return new(){Error=s.Error};}
    try {
    var temporary=Path.Combine(directory,Guid.NewGuid().ToString("N")+".new");SafeFiles.NoReparse(temporary);
    current.Position=0;
    using(var output=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None,81920,FileOptions.WriteThrough)) {await current.CopyToAsync(output,ct);output.Flush(true);}
    using(var verified=SafeFiles.OpenRead(temporary)) if(Convert.ToHexStringLower(await SHA256.HashDataAsync(verified,ct))!=hash) throw new IOException("Копия восстановления не прошла проверку.");
    File.Move(temporary,copy,false);
    }finally{ReleaseBytes(s,current.Length);}
   }
   using(var verified=SafeFiles.OpenRead(copy)) if(Convert.ToHexStringLower(await SHA256.HashDataAsync(verified,ct))!=hash) throw new IOException("Существующая копия восстановления сохранена, но не соответствует текущему DOCX.");
   s.RecoveryCommandId=commandId;s.RecoverySha256=hash;s.RecoveryPending=true;Persist(s);
  }
  // Retry the same durable copy/hash/command after lost ACK or service restart.
  using(var verified=SafeFiles.OpenRead(copy)) if(Convert.ToHexStringLower(await SHA256.HashDataAsync(verified,ct))!=s.RecoverySha256) throw new IOException("Копия восстановления повреждена; исходный DOCX и очередь сохранены.");
  if(hash!=s.RecoverySha256) {s.ErrorCode="file_busy";Persist(s);return new(){Error="Локальный DOCX изменился после создания копии. Сохранены обе версии; требуется проверка."};}
  if(s.RecoveryCommandId!=commandId) {
   // A newly authenticated browser command may replace an expired pre-commit
   // command while adopting the same verified bytes. No local copy is erased.
   var prior=s.RecoveryCommandId;s.RecoveryCommandId=commandId;s.RecoveryPending=true;Persist(s);
   if(prior!=null && s.ConnectionId!=null && agents.TryGetValue(s.ConnectionId,out var owner)) lock(owner) if(owner.Pending.ContainsKey(prior)) Complete(owner,prior,false);
  }
  using var response=await Send(s,HttpMethod.Post,"recover",JsonContent.Create(new{command_id=commandId,local_sha256=s.RecoverySha256,document_closed=true},options:Wire.Json),ct);
  await Ensure(response);
  var ack=await response.Content.ReadFromJsonAsync<ServerSession>(Wire.Json,ct) ?? throw new IOException();
  if(ack.SessionId!=s.SessionId || ack.State!="finished") throw new IOException("Сервер не подтвердил завершение восстановления.");
  s.RecoveryPending=false;s.RecoveryRevealPending=true;s.Finished=true;s.Status="finished";s.DocumentOpen=false;s.Error=null;s.ErrorCode=null;Persist(s);
  return new(){Ok=true,Session=views[s.SessionId]};
 }
}
