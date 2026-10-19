using ACTISync.Core;
using System.IO.Pipes;
using System.Security.Principal;
using System.Text;

namespace ACTISync.User;

internal static class Program {
 [STAThread]
 private static void Main(string[] args) {
  ApplicationConfiguration.Initialize();
  string? ticket=null;
  try { _=ClientConfig.Load(); if(args.Length>1) throw new ArgumentException(); if(args.Length==1) ticket=LaunchProtocol.Parse(args[0]); }
  catch { MessageBox.Show("ACTI Sync: недопустимая ссылка запуска или не настроен HTTPS-сервер. Обратитесь к администратору.","ACTI Sync",MessageBoxButtons.OK,MessageBoxIcon.Error); return; }
  var sid=WindowsIdentity.GetCurrent().User!.Value;
  using var mutex=new Mutex(true,@"Local\ACTISync.User."+sid,out var first);
  var ownPipe="ACTISync.User."+sid+"."+System.Diagnostics.Process.GetCurrentProcess().SessionId;
  if(!first) {
   try { using var p=new NamedPipeClientStream(".",ownPipe,PipeDirection.Out,PipeOptions.CurrentUserOnly); p.Connect(3000); using var writer=new StreamWriter(p,Encoding.UTF8){AutoFlush=true}; writer.WriteLine(ticket??"show"); } catch { MessageBox.Show("ACTI Sync уже запущен, но временно недоступен. Повторите запуск.","ACTI Sync"); }
   return;
  }
  using var window=new TrayWindow();
  using var cancel=new CancellationTokenSource();
  var listener=Task.Run(async()=> {
   while(!cancel.IsCancellationRequested) {
    using var pipe=new NamedPipeServerStream(ownPipe,PipeDirection.In,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous|PipeOptions.CurrentUserOnly);
    try { await pipe.WaitForConnectionAsync(cancel.Token); using var reader=new StreamReader(pipe); var input=await reader.ReadLineAsync(cancel.Token); if(input is null || input.Length>100) continue; window.BeginInvoke(()=> { if(input=="show") window.ShowWindow(); else if(System.Text.RegularExpressions.Regex.IsMatch(input,@"\A[A-Za-z0-9_-]{43}\z")) _=window.Launch(input); }); } catch(OperationCanceledException) { break; } catch(IOException) { }
   }
  });
  window.Shown+=(_,_)=> { if(ticket!=null) _=window.Launch(ticket); };
  Application.Run(window); cancel.Cancel();
 }
}
