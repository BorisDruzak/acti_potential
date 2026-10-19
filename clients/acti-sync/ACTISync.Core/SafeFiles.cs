using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
using System.Runtime.InteropServices;
using System.Text;

namespace ACTISync.Core;

public static class DurableJson {
 public static void Write<T>(string path, T value) {
  var temporary = path + ".new";
  using (var f = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough)) {
   JsonSerializer.Serialize(f, value, Wire.Json); f.Flush(true);
  }
  File.Move(temporary, path, true);
 }
 public static T? Read<T>(string path) => JsonSerializer.Deserialize<T>(File.ReadAllBytes(path), Wire.Json);
}
public static class SafeFiles {
 // Validate the actual object even when Word needs write/delete sharing. Snapshot
 // callers revalidate both this handle and the current target after copying.
 // Finish keeps the default restrictive sharing through the server commit.
 public static FileStream OpenRead(string path,bool allowWordWrites=false) {
  var canonical=Path.GetFullPath(path);
  var handle=CreateFile(canonical,0x80000000,allowWordWrites ? 7u : 1u,IntPtr.Zero,3,0x00200000|0x08000000,IntPtr.Zero);
  if(handle.IsInvalid) { handle.Dispose(); throw new IOException("Файл занят или недоступен."); }
  try {
   ValidateHandle(handle,canonical);
   return new FileStream(handle,FileAccess.Read);
  } catch { handle.Dispose(); throw; }
 }
 private static void ValidateHandle(SafeFileHandle handle,string path) {
   var canonical=Path.GetFullPath(path);
   if(!GetFileInformationByHandle(handle,out var info) || (info.Attributes & 0x400)!=0 || info.NumberOfLinks!=1) throw new IOException("Ссылки и переадресация файлов запрещены.");
   var buffer=new StringBuilder(32768); var length=GetFinalPathNameByHandle(handle,buffer,(uint)buffer.Capacity,0);
   if(length==0 || length>=buffer.Capacity) throw new IOException("Не удалось подтвердить путь файла.");
   var final=buffer.ToString(); if(final.StartsWith(@"\\?\UNC\",StringComparison.OrdinalIgnoreCase)) final=@"\\"+final[8..]; else if(final.StartsWith(@"\\?\",StringComparison.Ordinal)) final=final[4..];
   if(!string.Equals(Path.GetFullPath(final),canonical,StringComparison.OrdinalIgnoreCase)) throw new IOException("Файл перенаправлен вне рабочей папки.");
 }
 [StructLayout(LayoutKind.Sequential)] private struct FileInfoNative { public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime,LastAccessTime,LastWriteTime; public uint VolumeSerialNumber,FileSizeHigh,FileSizeLow,NumberOfLinks,FileIndexHigh,FileIndexLow; }
 [DllImport("kernel32.dll",EntryPoint="CreateFileW",CharSet=CharSet.Unicode,SetLastError=true)] private static extern SafeFileHandle CreateFile(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool GetFileInformationByHandle(SafeFileHandle handle,out FileInfoNative info);
 [DllImport("kernel32.dll",EntryPoint="GetFinalPathNameByHandleW",CharSet=CharSet.Unicode,SetLastError=true)] private static extern uint GetFinalPathNameByHandle(SafeFileHandle handle,StringBuilder path,uint length,uint flags);
 public static string FileName(string name) {
  if (string.IsNullOrWhiteSpace(name) || name.Length > 160 || name != Path.GetFileName(name) || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || name.Contains('/') || name.StartsWith("~$", StringComparison.Ordinal) || !name.EndsWith(".docx", StringComparison.OrdinalIgnoreCase) || name.EndsWith(' ') || name.EndsWith('.') || System.Text.RegularExpressions.Regex.IsMatch(name, @"\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\.", System.Text.RegularExpressions.RegexOptions.IgnoreCase)) throw new ArgumentException("Небезопасное имя DOCX.");
  return name;
 }
 public static void NoReparse(string path) {
  for (var info = new FileInfo(Path.GetFullPath(path)); info != null; info = info.Directory is { } parent ? new FileInfo(parent.FullName) : null)
   if ((File.Exists(info.FullName) || Directory.Exists(info.FullName)) && (File.GetAttributes(info.FullName) & FileAttributes.ReparsePoint) != 0) throw new IOException("Переадресация файлового пути запрещена.");
 }
 public static bool ValidDocx(string path) {
  try {
   using var stream = OpenRead(path);
   if (stream.Length < 1 || stream.Length > Wire.MaxFileSize) return false;
   using var zip = new ZipArchive(stream, ZipArchiveMode.Read);
   var content = zip.GetEntry("[Content_Types].xml"); var document = zip.GetEntry("word/document.xml");
   if (content is null || document is null || zip.Entries.Count > 10000) return false;
   // Read entries fully: a complete ZIP directory alone does not guarantee complete compressed data.
   long total = 0; var buffer = new byte[8192];
   foreach (var entry in zip.Entries) { total += entry.Length; if (total > 250L * 1024 * 1024) return false; using var input = entry.Open(); while (input.Read(buffer) != 0) { } }
   return true;
  } catch (Exception e) when (e is IOException or InvalidDataException or UnauthorizedAccessException) { return false; }
 }
 public static async Task<Snapshot?> SnapshotAsync(string workingPath, string snapshots, CancellationToken ct) {
  NoReparse(workingPath); NoReparse(snapshots);
  if (!File.Exists(workingPath)) return null;
  var before = new FileInfo(workingPath); var size = before.Length; var stamp = before.LastWriteTimeUtc;
  if (size > Wire.MaxFileSize || size == 0) return null;
  await Task.Delay(650, ct);
  before.Refresh(); if (!before.Exists || before.Length != size || before.LastWriteTimeUtc != stamp) return null;
  var path = Path.Combine(snapshots, Guid.NewGuid().ToString("N") + ".docx");
  try {
   NoReparse(workingPath);
   using var input = OpenRead(workingPath,allowWordWrites:true);
   if(input.Length!=size) return null;
   using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, FileOptions.WriteThrough)) {
    var buffer=new byte[81920]; long copied=0; int count;
    while((count=await input.ReadAsync(buffer,ct))!=0) { copied+=count; if(copied>Wire.MaxFileSize) throw new IOException("Размер DOCX превысил 25 МиБ во время сохранения."); await output.WriteAsync(buffer.AsMemory(0,count),ct); }
    output.Flush(true);
   }
   before.Refresh(); if (!before.Exists || before.Length != size || before.LastWriteTimeUtc != stamp || !ValidDocx(path)) { File.Delete(path); return null; }
   using var f = File.OpenRead(path);
   var hash = Convert.ToHexStringLower(await SHA256.HashDataAsync(f, ct));
   input.Position=0; var reread=Convert.ToHexStringLower(await SHA256.HashDataAsync(input,ct));
   ValidateHandle(input.SafeFileHandle,workingPath);
   using var current=OpenRead(workingPath,allowWordWrites:true);
   var currentHash=Convert.ToHexStringLower(await SHA256.HashDataAsync(current,ct));
   ValidateHandle(current.SafeFileHandle,workingPath);
   before.Refresh();
   if(hash!=reread || hash!=currentHash || !before.Exists || before.Length!=size || before.LastWriteTimeUtc!=stamp) { f.Dispose(); File.Delete(path); return null; }
   return new Snapshot { Path=path, Hash=hash };
  } catch { if (File.Exists(path)) File.Delete(path); throw; }
 }
}
