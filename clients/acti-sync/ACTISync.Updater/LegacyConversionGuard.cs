using ACTISync.Core;
using Microsoft.Win32.SafeHandles;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace ACTISync.Updater;

// An old User cannot acknowledge drain. Its converter holds original.* open
// until COM cleanup finishes, including native COM calls that outlive a timeout.
// Read-only exclusive handles prove those sources are idle and prevent a late
// conversion from starting while the old message loop exits. Never modify bytes.
internal sealed class LegacyConversionGuard:IDisposable {
 private readonly List<SafeFileHandle> held=[];
 internal static LegacyConversionGuard Acquire(IEnumerable<string> sids,string? testRoot=null) {
  var guard=new LegacyConversionGuard();var root=testRoot??Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"ACTI","Sync");var state=Path.Combine(root,"state");
  try {
   if(!Directory.Exists(state))return guard;SafeFiles.NoReparse(state);var owners=sids.ToHashSet(StringComparer.Ordinal);var files=Directory.GetFiles(state,"*.json");if(files.Length>10000)throw new IOException("Legacy state limit exceeded.");
   foreach(var file in files) {
    using var input=SafeFiles.OpenRead(file);if(input.Length>1024*1024)throw new IOException("Legacy state is oversized.");using var json=JsonDocument.Parse(input,new JsonDocumentOptions{MaxDepth=16});var data=json.RootElement;
    var sid=data.GetProperty("sid").GetString();var format=data.GetProperty("source_format").GetString();
    if(sid==null || !owners.Contains(sid) || format=="docx")continue;
    if(format==null || !SafeFiles.WordSourceFormat(format) || !Guid.TryParse(data.GetProperty("session_id").GetString(),out var id))throw new IOException("Legacy conversion state is invalid.");
    var source=Path.Combine(root,"work",sid,id.ToString("D"),"original."+format);if(!File.Exists(source))continue;SafeFiles.NoReparse(source);
    var handle=CreateFile(source,0x80000000,0,IntPtr.Zero,3,0x00200000|0x08000000,IntPtr.Zero);
    if(handle.IsInvalid){handle.Dispose();throw new IOException("Legacy native conversion is still active; bootstrap deferred.");}
    guard.held.Add(handle);
    if(!GetFileInformationByHandle(handle,out var info) || (info.Attributes&0x400)!=0 || info.Links!=1)throw new IOException("Unsafe legacy conversion source.");
    var final=new StringBuilder(32768);var length=GetFinalPathNameByHandle(handle,final,(uint)final.Capacity,0);
    if(length==0 || length>=final.Capacity || !string.Equals(final.ToString(),@"\\?\"+Path.GetFullPath(source),StringComparison.OrdinalIgnoreCase))throw new IOException("Legacy source was redirected.");
   }
   return guard;
  } catch {guard.Dispose();throw;}
 }
 public void Dispose(){foreach(var file in held)file.Dispose();held.Clear();}
 [StructLayout(LayoutKind.Sequential)]private struct Info {public uint Attributes;public System.Runtime.InteropServices.ComTypes.FILETIME Creation,Access,Write;public uint Volume,High,Low,Links,IndexHigh,IndexLow;}
 [DllImport("kernel32.dll",EntryPoint="CreateFileW",CharSet=CharSet.Unicode,SetLastError=true)]private static extern SafeFileHandle CreateFile(string path,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll")]private static extern bool GetFileInformationByHandle(SafeFileHandle handle,out Info info);
 [DllImport("kernel32.dll",EntryPoint="GetFinalPathNameByHandleW",CharSet=CharSet.Unicode)]private static extern uint GetFinalPathNameByHandle(SafeFileHandle handle,StringBuilder path,uint length,uint flags);
}
