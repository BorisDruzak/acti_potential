using ACTISync.Core;

namespace ACTISync.Service;

public sealed partial class SyncEngine {
 private bool ReserveBytes(SessionState s,long bytes) {
  lock(quotaGate) {
   long Stored(string directory) {
    if(!Directory.Exists(directory))return 0;
    SafeFiles.NoReparse(directory);
    return Directory.EnumerateFiles(directory).Sum(path=>{try{return new FileInfo(path).Length;}catch(FileNotFoundException){return 0L;}});
   }
   var stored=sessions.Values.Where(x=>x.Sid==s.Sid).Sum(x=>Stored(Snapshots(x))+Stored(Path.GetDirectoryName(RecoveryPath(x))!));
   var reserved=reservedBytes.GetValueOrDefault(s.Sid);
   // Reservation includes temporary copy space and is intentionally conservative
   // during concurrent publication. No session holds this lock while copying.
   if(bytes<0 || stored+reserved+bytes>1024L*1024*1024 || new DriveInfo(Path.GetPathRoot(root)!).AvailableFreeSpace-reservedBytes.Values.Sum()-bytes<256L*1024*1024) return false;
   reservedBytes[s.Sid]=reserved+bytes;return true;
  }
 }
 private void ReleaseBytes(SessionState s,long bytes) {lock(quotaGate)reservedBytes[s.Sid]-=bytes;}
 private void QuotaError(SessionState s) {s.ErrorCode="quota";s.Status="error";s.Error="Недостаточно места в очереди (лимит 1 ГиБ на пользователя). Локальные файлы сохранены; освободите место и нажмите «Повторить».";Persist(s);}
}
