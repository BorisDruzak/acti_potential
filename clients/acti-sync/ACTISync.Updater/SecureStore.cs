using ACTISync.Core;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;

namespace ACTISync.Updater;

internal static class SecureStore {
 internal static readonly string Root=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"ACTI","SyncUpdater");
 internal static readonly string Public=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"ACTI","SyncUpdateStatus");
 internal static string Request=>Path.Combine(Root,"request.json");
 internal static string Envelope=>Path.Combine(Root,"release.json");
 internal static string Package=>Path.Combine(Root,"package.msi");
 internal static string InstalledDirectory=>Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),"ACTI","Sync");
 internal static string UpdaterExe=>Path.Combine(InstalledDirectory,"ACTISync.Updater.exe");
 internal static string HelperDirectory=>Path.Combine(Root,"helper");
 internal static string HelperExe=>Path.Combine(HelperDirectory,"ACTISync.Updater.exe");
 internal static string UserExe=>Path.Combine(InstalledDirectory,"ACTISync.User.exe");
 internal static void RequireSystem(bool helper=false) { if(!WindowsIdentity.GetCurrent().IsSystem || !string.Equals(Path.GetFullPath(Environment.ProcessPath!),helper?HelperExe:UpdaterExe,StringComparison.OrdinalIgnoreCase)) throw new UnauthorizedAccessException("Updater must run as the fixed SYSTEM component."); SafeFiles.NoReparse(Environment.ProcessPath!);if(helper)HelperImage.Verify(); }
 internal static void Initialize() { DirectoryAcl(Path.GetDirectoryName(Root)!,true); DirectoryAcl(Root,false); DirectoryAcl(HelperDirectory,false); DirectoryAcl(Public,true); }
 private static void DirectoryAcl(string path,bool readable) {
  // Refuse pre-planted links and foreign-owned objects before granting access.
  SafeFiles.NoReparse(path);
  if(Directory.Exists(path)) { var owner=new DirectoryInfo(path).GetAccessControl().GetOwner(typeof(SecurityIdentifier))?.Value; if(owner!="S-1-5-18" && owner!="S-1-5-32-544") throw new UnauthorizedAccessException("Untrusted updater directory owner."); }
  Directory.CreateDirectory(path); SafeFiles.NoReparse(path);
  var acl=new DirectorySecurity(); acl.SetAccessRuleProtection(true,false); acl.SetOwner(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null));
  var inherit=InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit;
  foreach(var sid in new[]{WellKnownSidType.LocalSystemSid,WellKnownSidType.BuiltinAdministratorsSid}) acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid,null),FileSystemRights.FullControl,inherit,PropagationFlags.None,AccessControlType.Allow));
  if(readable) acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid,null),FileSystemRights.ReadAndExecute,inherit,PropagationFlags.None,AccessControlType.Allow));
  new DirectoryInfo(path).SetAccessControl(acl);
  // An existing child with a deliberately permissive explicit ACL is not reused.
  foreach(var file in Directory.EnumerateFiles(path)) { SafeFiles.NoReparse(file); using(var stream=SafeFiles.OpenRead(file)){} var security=new FileSecurity(); security.SetAccessRuleProtection(false,false); security.SetOwner(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null)); new FileInfo(file).SetAccessControl(security); }
 }
 internal static T? Read<T>(string path) { if(!File.Exists(path)) return default; SafeFiles.NoReparse(path); using var input=SafeFiles.OpenRead(path); if(input.Length>1024*1024) throw new InvalidDataException(); return JsonSerializer.Deserialize<T>(input,Wire.Json); }
 internal static void Write<T>(string path,T value) {
  SafeFiles.NoReparse(path); var temporary=Path.Combine(Path.GetDirectoryName(path)!,Guid.NewGuid().ToString("N")+".new");
  using(var f=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None,4096,FileOptions.WriteThrough)) { JsonSerializer.Serialize(f,value,Wire.Json); f.Flush(true); }
  if(File.Exists(path)) { using(var old=SafeFiles.OpenRead(path)){} } File.Move(temporary,path,true);
 }
 internal static void WriteEnvelope(byte[] bytes) { var temp=Envelope+".new"; Delete(temp); using(var stream=new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None,4096,FileOptions.WriteThrough)){stream.Write(bytes);stream.Flush(true);} File.Move(temp,Envelope,true); }
 internal static byte[] ReadEnvelope() { using var input=SafeFiles.OpenRead(Envelope); if(input.Length>ReleaseTrust.MaxEnvelopeSize) throw new InvalidDataException(); using var output=new MemoryStream();input.CopyTo(output);return output.ToArray(); }
 internal static void Delete(string path) { if(!File.Exists(path))return; SafeFiles.NoReparse(path); using(var input=SafeFiles.OpenRead(path)){} File.Delete(path); }
 internal static void Status(string state,string? version=null)=>Write(Path.Combine(Public,"status.json"),new{state,version=version??CurrentVersion,updated_at=DateTimeOffset.UtcNow});
 internal static void Failure(string stage,Exception error)=>Write(Path.Combine(Root,"failure.json"),new{
  stage,error_type=error.GetType().Name,error_code=error.HResult,native_error=error is System.ComponentModel.Win32Exception native?native.NativeErrorCode:(int?)null,
  error_site=error.TargetSite?.DeclaringType?.FullName+"."+error.TargetSite?.Name,
  // Method names only: never persist exception messages, arguments, source paths,
  // document names, credentials, configuration values, or user identities.
  stack_methods=new System.Diagnostics.StackTrace(error,false).GetFrames().Take(12).Select(frame=>frame.GetMethod()?.DeclaringType?.FullName+"."+frame.GetMethod()?.Name).ToArray(),at=DateTimeOffset.UtcNow});
 internal static string CurrentVersion=>typeof(SecureStore).Assembly.GetName().Version!.ToString(3);
}
internal sealed class UpdateRequest {
 public string Version {get;set;}="";
 public string Phase {get;set;}="staged";
 public List<UserProcess> Users {get;set;}=[];
 public int InstallerPid {get;set;}
 public long InstallerStarted {get;set;}
 public uint InstallerThreadId {get;set;}
 public int HelperPid {get;set;}
 public long HelperStarted {get;set;}
 public int? InstallerExitCode {get;set;}
}
internal sealed record UserProcess(int Pid,long Started,string Sid,int SessionId,uint ThreadId);
