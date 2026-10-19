using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace ACTISync.Core;

public static class Wire {
 public static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, PropertyNameCaseInsensitive = true };
 public const string PipeName = "ACTISync.v1";
 public static string Version => typeof(Wire).Assembly.GetName().Version!.ToString(3);
 public const long MaxFileSize = 25 * 1024 * 1024;
}
public static class LaunchProtocol {
 public static IpcRequest ParseInvocation(string input) {
  var match=Regex.Match(input,@"\Aacti-sync://(launch|connect)/?\?ticket=([A-Za-z0-9_-]{43})\z",RegexOptions.CultureInvariant);
  if(!match.Success) throw new ArgumentException("Недопустимая ссылка ACTI Sync.");
  return new(){Action=match.Groups[1].Value,Ticket=match.Groups[2].Value};
 }
 public static string Parse(string input) {
  var match = Regex.Match(input, @"\Aacti-sync://launch/?\?ticket=([A-Za-z0-9_-]{43})\z", RegexOptions.CultureInvariant);
  if (!match.Success) throw new ArgumentException("Недопустимая ссылка запуска ACTI Sync.");
  return match.Groups[1].Value;
 }
}
public sealed record ClientConfig(Uri Origin) {
 public bool Matches(string storedOrigin) => string.Equals(Origin.AbsoluteUri,storedOrigin,StringComparison.Ordinal);
 public static ClientConfig Load() {
  using var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\ACTI\Sync");
  var raw = key?.GetValue("ServerOrigin") as string;
  if (!Uri.TryCreate(raw, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.UserInfo.Length != 0 || uri.AbsolutePath != "/" || uri.Query.Length != 0 || uri.Fragment.Length != 0)
   throw new InvalidOperationException("Администратор должен настроить HTTPS ServerOrigin в HKLM\\SOFTWARE\\ACTI\\Sync.");
  return new ClientConfig(uri);
 }
 public Uri Api(string suffix) => new(Origin, "/api/v1/office-sync/" + suffix);
}
public sealed class ServerSession {
 public string SourceFormat { get; set; } = "docx";
 public AgentCredentials? Agent { get; set; }
 public string SessionId { get; set; } = "";
 public string? Token { get; set; }
 public string FileId { get; set; } = "";
 public string Filename { get; set; } = "";
 public int Revision { get; set; }
 public string Sha256 { get; set; } = "";
 public DateTimeOffset ExpiresAt { get; set; }
 public DateTimeOffset LeaseExpiresAt { get; set; }
 public string State { get; set; } = "editing";
 public DateTimeOffset? LastSavedAt { get; set; }
}
public sealed class Snapshot {
 public string Path { get; set; } = "";
 public string Hash { get; set; } = "";
 public string Key { get; set; } = Guid.NewGuid().ToString("N");
 public int ExpectedRevision { get; set; }
}
public sealed class SessionState {
 public string SourceFormat { get; set; } = "docx";
 public string? ConversionSourcePath { get; set; }
 public string? ConversionSourceSha256 { get; set; }
 public string? ConnectionId { get; set; }
 public bool? DocumentOpen { get; set; }
 public bool PresenceDirty { get; set; }
 public string ServerOrigin { get; set; } = "";
 public string SessionId { get; set; } = "";
 public string Sid { get; set; } = "";
 public string FileId { get; set; } = "";
 public string FileName { get; set; } = "";
 public string WorkingPath { get; set; } = "";
 public string ProtectedToken { get; set; } = "";
 public string AckHash { get; set; } = "";
 public int Revision { get; set; }
 public DateTimeOffset ExpiresAt { get; set; }
 public DateTimeOffset LeaseExpiresAt { get; set; }
 public DateTimeOffset? LastAck { get; set; }
 public string Status { get; set; } = "opening";
 public string? Error { get; set; }
 public Snapshot? Pending { get; set; }
 public Snapshot? NextPending { get; set; }
 public int Failures { get; set; }
 public DateTimeOffset RetryAt { get; set; }
 public bool Opened { get; set; }
 public bool Finished { get; set; }
 [JsonIgnore] public bool Blocked => Status is "conflict" or "reauth" or "error";
 [JsonIgnore] public bool Dirty { get; set; }
}
public sealed class IpcRequest {
 public string? CommandId { get; set; }
 public bool? Ok { get; set; }
 public string Action { get; set; } = "list";
 public string? Ticket { get; set; }
 public string? SessionId { get; set; }
}
public sealed class SessionView {
 public string SourceFormat { get; set; } = "docx";
 public string? ConversionSourcePath { get; set; }
 public string? ConversionSourceSha256 { get; set; }
 public bool? DocumentOpen { get; set; }
 public string SessionId { get; set; } = "";
 public string FileName { get; set; } = "";
 public string WorkingPath { get; set; } = "";
 public string Status { get; set; } = "";
 public string? Error { get; set; }
 public int Revision { get; set; }
 public DateTimeOffset? LastAck { get; set; }
 public bool HasPending { get; set; }
 public static SessionView From(SessionState s) => new() { SourceFormat=s.SourceFormat,ConversionSourcePath=s.ConversionSourcePath,ConversionSourceSha256=s.ConversionSourceSha256,DocumentOpen=s.DocumentOpen, SessionId=s.SessionId, FileName=s.FileName, WorkingPath=s.WorkingPath, Status=s.Status, Error=s.Error, Revision=s.Revision, LastAck=s.LastAck, HasPending=s.Pending!=null || s.NextPending!=null };
}
public sealed class IpcResponse {
 public bool AgentOnline { get; set; }
 public List<AgentCommand> Commands { get; set; } = [];
 public bool Ok { get; set; }
 public string? Error { get; set; }
 public SessionView? Session { get; set; }
 public List<SessionView> Sessions { get; set; } = [];
}
public sealed class AgentCredentials {
 public string ConnectionId { get; set; } = "";
 public string Token { get; set; } = "";
 public DateTimeOffset ExpiresAt { get; set; }
}
public sealed class AgentCommand {
 public DateTimeOffset ExpiresAt { get; set; }
 public string Id { get; set; } = "";
 public string SessionId { get; set; } = "";
 public string Action { get; set; } = "";
}
public sealed class CommandResult {
 public string Id { get; set; } = "";
 public bool Ok { get; set; }
 public string? Error { get; set; }
}
public sealed class AgentHeartbeat {
 public List<AgentCommand> Commands { get; set; } = [];
}
public sealed class AgentState {
 public string ServerOrigin { get; set; } = "";
 public string Sid { get; set; } = "";
 public string ConnectionId { get; set; } = "";
 public string ProtectedToken { get; set; } = "";
 public DateTimeOffset ExpiresAt { get; set; }
 public List<CommandResult> Results { get; set; } = [];
 public Dictionary<string,CommandResult> Completed { get; set; } = [];
 public Dictionary<string,AgentCommand> Pending { get; set; } = [];
 public HashSet<string> Started { get; set; } = [];
}
public static class AgentPresence {
 public static bool IsFresh(DateTimeOffset? pulse,DateTimeOffset now) => pulse.HasValue && now>=pulse.Value && now-pulse.Value<=TimeSpan.FromSeconds(15);
}
