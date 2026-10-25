using ACTISync.Core;
using System.Net;
using System.Net.Http.Json;
using System.Text.RegularExpressions;

namespace ACTISync.Service;

public sealed partial class SyncEngine {
 private async Task<bool> ResumeLease(SessionState s,CancellationToken ct) {
  if(s.LeaseExpiresAt==default || s.LeaseExpiresAt>DateTimeOffset.UtcNow)return true;
  // Never extend an expired credential, rebase pending bytes, or claim a lease
  // using the hash of the newer, unacknowledged local snapshot.
  if(s.ExpiresAt<=DateTimeOffset.UtcNow) {
   s.Status="reauth";s.ErrorCode="reauth";s.Error="Доступ истёк. Локальная копия и очередь сохранены.";Persist(s);return false;
  }
  if(s.Revision<1 || s.AckHash is null || !Regex.IsMatch(s.AckHash,"\\A[0-9a-f]{64}\\z"))return false;
  var pending=s.Pending;
  using var response=await Send(s,HttpMethod.Post,"resume",JsonContent.Create(new{revision=s.Revision,sha256=s.AckHash,pending=pending is null ? null : new{key=pending.Key,revision=pending.ExpectedRevision,sha256=pending.Hash}},options:Wire.Json),ct);
  // An older server has no optional resume route. Keep its conflict behavior;
  // do not downgrade to a write, refresh credentials, or discard the queue.
  if(response.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.MethodNotAllowed) {
   s.Status="conflict";s.ErrorCode="conflict";s.Error="Блокировка истекла. Локальная копия и очередь сохранены.";s.RetryAt=DateTimeOffset.UtcNow.AddSeconds(60);Persist(s);return false;
  }
  await Ensure(response);
  var lease=await response.Content.ReadFromJsonAsync<ServerSession>(Wire.Json,ct) ?? throw new IOException();
  var priorAck=lease.Revision==s.Revision && lease.Sha256==s.AckHash;
  var ownCommit=pending!=null && pending.ExpectedRevision==s.Revision && lease.Revision==s.Revision+1 && lease.Sha256==pending.Hash;
  if(!Guid.TryParse(s.SessionId,out var expectedSession) || !Guid.TryParse(s.FileId,out var expectedFile) ||
   !Guid.TryParse(lease.SessionId,out var session) || session!=expectedSession ||
   !Guid.TryParse(lease.FileId,out var file) || file!=expectedFile ||
   !(priorAck || ownCommit) || lease.LeaseExpiresAt<=DateTimeOffset.UtcNow)
   throw new IOException("ACTI не подтвердил восстановление блокировки.");
  LearnCapabilities(s,lease);s.LeaseExpiresAt=lease.LeaseExpiresAt;
  // Even an exact committed-receipt proof never advances local ACK or changes
  // pending.ExpectedRevision: normal same-key save replay must confirm it first.
  s.Status=s.Pending!=null ? "local_saved" : s.Opened ? "editing" : "opening";
  s.Error=null;s.ErrorCode=null;s.Failures=0;s.RetryAt=DateTimeOffset.MinValue;Persist(s);
  return true;
 }
}
