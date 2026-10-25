using ACTISync.Core;
using System.IO.Compression;
using System.Text;

if(args.Contains("--multifile")) { await MultifileChecks.Run(); return; }
if(args.Contains("--recovery")) { await RecoveryChecks.Run(); return; }
if(args.Contains("--deadline")) { await HttpDeadlineChecks.Run(); return; }
if(args.Contains("--telemetry")) { await TelemetryNegotiationChecks.Run(); return; }
if(args.Contains("--lease-resume")) { await LeaseResumeChecks.Run(); return; }
if(args.Contains("--agent-retry")) { await AgentRetryChecks.Run(); return; }
if(args.Contains("--service-pipe")) { await ServicePipeChecks.Run(); return; }

static void Check(bool condition, string name) { if (!condition) throw new Exception(name); Console.WriteLine("PASS " + name); }
static void Reject(Action action, string name) { try { action(); } catch (ArgumentException) { Console.WriteLine("PASS " + name); return; } throw new Exception(name); }
Check(LaunchProtocol.Parse("acti-sync://launch?ticket=" + new string('a', 43)) == new string('a', 43), "strict launch ticket");
Check(LaunchProtocol.Parse("acti-sync://launch/?ticket=" + new string('a', 43)) == new string('a', 43), "browser-normalized launch authority");
Check(LaunchProtocol.ParseInvocation("acti-sync://connect?ticket=" + new string('b',43)).Action == "connect", "strict headless connect protocol");
var configured = new ClientConfig(new Uri("https://example.com"));
var maintenanceNow=DateTimeOffset.UtcNow;
Check(NativeDispatchMaintenance.ActiveAt(maintenanceNow.AddMinutes(3).ToString("O"),maintenanceNow) && !NativeDispatchMaintenance.ActiveAt(maintenanceNow.AddSeconds(-1).ToString("O"),maintenanceNow) && !NativeDispatchMaintenance.ActiveAt(maintenanceNow.AddMinutes(5).ToString("O"),maintenanceNow) && !NativeDispatchMaintenance.ActiveAt(null,maintenanceNow),"native dispatch pause expires and cannot persist beyond its bounded deadline");
Check(configured.Matches("https://example.com/") && !configured.Matches("https://other.example.com/") && !configured.Matches(""), "recovered credentials remain bound to installed server origin");
foreach (var uri in new[] { "https://launch?ticket=" + new string('a', 43), "acti-sync://launch?ticket=x", "acti-sync://launch?ticket=" + new string('a', 43) + "&url=https://evil", "acti-sync://launch/path?ticket=" + new string('a', 43), "acti-sync://launch/evil?ticket=" + new string('a',43), "acti-sync://launch//?ticket=" + new string('a',43) }) Reject(() => LaunchProtocol.Parse(uri), "reject URI injection");
foreach (var name in new[] { "../x.docx", "x/evil.docx", "~$lock.docx", "CON.docx", "x.docx:evil", "x.exe" }) Reject(() => SafeFiles.FileName(name), "reject unsafe filename");
var root = Path.Combine(Path.GetTempPath(), "ACTISync-tests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
try {
 var working = Path.Combine(root, "working.docx");
 void WriteDoc(string value) { var temp = working + ".tmp"; using (var z = ZipFile.Open(temp, ZipArchiveMode.Create)) { using (var w = new StreamWriter(z.CreateEntry("[Content_Types].xml").Open())) w.Write("<Types />"); using (var w = new StreamWriter(z.CreateEntry("word/document.xml").Open())) w.Write(value); } File.Move(temp, working, true); }
 WriteDoc("first");
 var first = await SafeFiles.SnapshotAsync(working, root, CancellationToken.None);
 Check(first is not null && File.Exists(first.Path), "durable snapshot of Word safe-save");
 WriteDoc("second");
 var second = await SafeFiles.SnapshotAsync(working, root, CancellationToken.None);
 Check(second is not null && second.Hash != first!.Hash && File.Exists(first.Path), "new snapshot preserves pending bytes");
 using(var wordHandle=new FileStream(working,FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite|FileShare.Delete)) {
  Check(!SafeFiles.ValidDocx(working),"finish validation still refuses Word's writer handle");
  Check(SafeFiles.ValidDocx(working,allowWordWrites:true),"reopen validates DOCX while Word retains its writer handle");
  var live=await SafeFiles.SnapshotAsync(working,root,CancellationToken.None);
  Check(live is not null && live.Hash==second!.Hash,"live Word writer handle permits validated stable snapshot");
 }
 var replacing=Task.Run(async()=>{ await Task.Delay(150); WriteDoc("safe-save replacement"); });
 var duringReplace=await SafeFiles.SnapshotAsync(working,root,CancellationToken.None); await replacing;
 Check(duringReplace is null,"safe-save replacement during stability window is deferred");
 Check(await SafeFiles.SnapshotAsync(working,root,CancellationToken.None) is not null,"completed safe-save replacement becomes synchronizable");
 var state = new SessionState { SessionId = Guid.NewGuid().ToString(), Sid = "S-1-5-21-123", FileName = "working.docx", Pending = second };
 var path = Path.Combine(root, "state.json"); DurableJson.Write(path, state);
 Check(DurableJson.Read<SessionState>(path)?.Pending?.Hash == second!.Hash, "pending recovery after state reload");
 Check(!Directory.EnumerateFiles(root, "*.new").Any(), "atomic state publication cleans temporary files");
 File.WriteAllText(working, "incomplete save");
 Check(!SafeFiles.ValidDocx(working,allowWordWrites:true),"reopen refuses incomplete DOCX");
 Check(await SafeFiles.SnapshotAsync(working, root, CancellationToken.None) is null, "incomplete DOCX never queued");
 var redirected=Path.Combine(root,"redirect.docx");
 File.CreateSymbolicLink(redirected,working);
 Check(!SafeFiles.ValidDocx(redirected,allowWordWrites:true),"reopen refuses redirected object");
 try { using var ignored=SafeFiles.OpenRead(redirected); throw new Exception("symlink accepted"); } catch(IOException) { Console.WriteLine("PASS reparse object rejected through opened handle"); }
 var junction=Path.Combine(root,"redirect-dir"); Directory.CreateSymbolicLink(junction,root);
 try { using var ignored=SafeFiles.OpenRead(Path.Combine(junction,"working.docx")); throw new Exception("ancestor link accepted"); } catch(IOException) { Console.WriteLine("PASS parent redirection rejected through final handle path"); }
 File.Delete(redirected); Directory.Delete(junction);
 var hardlink=Path.Combine(root,"hardlink.docx");
 if(!NativeLinks.CreateHardLink(hardlink,working,IntPtr.Zero)) throw new Exception("hardlink test fixture failed");
 Check(!SafeFiles.ValidDocx(hardlink,allowWordWrites:true),"reopen refuses hard-linked object");
 try { using var ignored=SafeFiles.OpenRead(hardlink); throw new Exception("hardlink accepted"); } catch(IOException) { Console.WriteLine("PASS hardlink object rejected through opened handle"); }
 File.Delete(hardlink);
 Console.WriteLine("ALL WINDOWS CORE CHECKS PASSED");
} finally { Directory.Delete(root, true); }
await UserPipeChecks.Run();
await ServicePipeChecks.Run();
await BrowserBridgeChecks.Run();
await EngineChecks.Run();
await MultifileChecks.Run();
await RecoveryChecks.Run();
await HttpDeadlineChecks.Run();
await TelemetryNegotiationChecks.Run();
await LeaseResumeChecks.Run();
await AgentChecks.Run();
await AgentRetryChecks.Run();
internal static class NativeLinks {
 [System.Runtime.InteropServices.DllImport("kernel32.dll",EntryPoint="CreateHardLinkW",CharSet=System.Runtime.InteropServices.CharSet.Unicode,SetLastError=true)]
 [return:System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)] internal static extern bool CreateHardLink(string link,string target,IntPtr security);
}
