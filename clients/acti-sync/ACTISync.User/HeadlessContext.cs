using ACTISync.Core;
using Microsoft.Win32;
using System.Diagnostics;
using System.IO.Pipes;
using System.Text.Json;

namespace ACTISync.User;

// This STA loop dispatches protocol requests and native Word/Explorer operations.
// It creates no Form, notification icon, taskbar entry or dialog.
internal sealed class HeadlessContext : ApplicationContext {
 private readonly CancellationTokenSource cancellation=new();
 private readonly System.Windows.Forms.Timer timer=new(){Interval=4000};
 private readonly SynchronizationContext dispatch;
 private readonly HashSet<string> executing=[];
 private readonly HashSet<string> opening=[];
 private bool pulsing;
 internal HeadlessContext(string ownPipe,IpcRequest? invocation) {
  SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
  dispatch=SynchronizationContext.Current!;
  timer.Tick+=async (_,_)=>await Pulse(); timer.Start();
  _=Listen(ownPipe);
  dispatch.Post(async _=> { await Pulse(); if(invocation!=null) await Invoke(invocation); },null);
 }
 private async Task Listen(string name) {
  while(!cancellation.IsCancellationRequested) {
   using var pipe=new NamedPipeServerStream(name,PipeDirection.In,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous|PipeOptions.CurrentUserOnly);
   try {
    await pipe.WaitForConnectionAsync(cancellation.Token);
    using var timeout=CancellationTokenSource.CreateLinkedTokenSource(cancellation.Token); timeout.CancelAfter(5000);
    using var reader=new StreamReader(pipe); var buffer=new char[1]; var input=new System.Text.StringBuilder();
    while(await reader.ReadAsync(buffer.AsMemory(),timeout.Token)!=0 && buffer[0]!='\n') { input.Append(buffer[0]); if(input.Length>512) throw new IOException(); }
    var request=JsonSerializer.Deserialize<IpcRequest>(input.ToString(),Wire.Json);
    if(request?.Action is "launch" or "connect" && request.Ticket!=null) {
     var valid=LaunchProtocol.ParseInvocation($"acti-sync://{request.Action}?ticket={request.Ticket}");
     dispatch.Post(async _=>await Invoke(valid),null);
    }
   } catch(OperationCanceledException) { if(cancellation.IsCancellationRequested) break; }
   catch(Exception e) when(e is IOException or JsonException or ArgumentException) { }
  }
 }
 private static async Task Invoke(IpcRequest request) {
  try { var result=await ServiceClient.Send(request); if(request.Action=="launch" && result.Ok && result.Session is {Status:not "conflict"} session && !await OpenWord(session)) await ServiceClient.Send(new(){Action="open_failed",SessionId=session.SessionId}); }
  catch { }
 }
 private async Task Pulse() {
  if(pulsing) return; pulsing=true;
  try {
   var response=await ServiceClient.Send(new(){Action="pulse"});
   foreach(var command in response.Commands) if(executing.Add(command.Id)) _=Execute(command);
  } catch { } finally { pulsing=false; }
 }
 private async Task Execute(AgentCommand command) {
  try {
   var request=new IpcRequest {Action=command.Action,SessionId=command.SessionId,CommandId=command.Id};
   if(command.Action is "open" or "reveal") {
    request.Action="command_start";
    var result=await ServiceClient.Send(request);
    if(!result.Ok || result.Session is null) return;
    var ok=false;
    try {
     if(command.Action=="open") {
      if(opening.Add(command.SessionId)) { try { ok=await OpenWord(result.Session); } finally { opening.Remove(command.SessionId); } }
     } else {
      SafeFiles.NoReparse(result.Session.WorkingPath);
      using(var file=SafeFiles.OpenRead(result.Session.WorkingPath,true)) {}
      var start=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),"explorer.exe")){UseShellExecute=false};
      start.ArgumentList.Add("/select,"+result.Session.WorkingPath); Process.Start(start)?.Dispose(); ok=true;
     }
    } catch { }
    await ServiceClient.Send(new(){Action="command_result",SessionId=command.SessionId,CommandId=command.Id,Ok=ok});
   } else if(command.Action is "retry" or "finish") await ServiceClient.Send(request);
  } catch { } finally { executing.Remove(command.Id); }
 }
 private static string? WordPath() {
  foreach(var view in new[]{RegistryView.Registry64,RegistryView.Registry32}) {
   using var hklm=RegistryKey.OpenBaseKey(RegistryHive.LocalMachine,view); using var key=hklm.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE");
   var value=key?.GetValue(null) as string;
   if(value!=null && Path.IsPathFullyQualified(value) && File.Exists(value)) return value;
  }
  foreach(var folder in new[]{Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86)}) foreach(var suffix in new[]{@"Microsoft Office\root\Office16\WINWORD.EXE",@"Microsoft Office\Office16\WINWORD.EXE"}) { var path=Path.Combine(folder,suffix); if(File.Exists(path)) return path; }
  return null;
 }
 private static async Task<bool> OpenWord(SessionView session) {
  try {
   var exe=WordPath(); if(exe is null) { await ServiceClient.Send(new(){Action="word_missing",SessionId=session.SessionId}); return false; }
   SafeFiles.NoReparse(session.WorkingPath);
   if(session.SourceFormat!="docx" && !File.Exists(session.WorkingPath)) await WordSourceConversion.ConvertAsync(session);
   if(!SafeFiles.ValidDocx(session.WorkingPath)) return false;
   var start=new ProcessStartInfo(exe){UseShellExecute=false}; start.ArgumentList.Add(session.WorkingPath); Process.Start(start)?.Dispose();
   for(var i=0;i<40;i++) {
    if(Directory.EnumerateFiles(Path.GetDirectoryName(session.WorkingPath)!,"~$*.docx").Any()) return (await ServiceClient.Send(new(){Action="opened",SessionId=session.SessionId})).Ok;
    await Task.Delay(500);
   }
  } catch { }
  return false;
 }
 protected override void Dispose(bool disposing) { if(disposing) { timer.Dispose(); cancellation.Cancel(); cancellation.Dispose(); } base.Dispose(disposing); }
}
