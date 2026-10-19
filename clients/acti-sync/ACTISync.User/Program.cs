using ACTISync.Core;
using System.Security.Principal;
using System.Text.Json;

namespace ACTISync.User;

internal static class Program {
 [STAThread]
 private static void Main(string[] args) {
  try {
   Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
   Application.ThreadException+=(_,_)=>{};
   ApplicationConfiguration.Initialize();
   _=ClientConfig.Load();
   if(args.Length>1) return;
   var invocation=args.Length==1 ? LaunchProtocol.ParseInvocation(args[0]) : null;
   var sid=WindowsIdentity.GetCurrent().User!.Value;
   using var mutex=new Mutex(true,@"Local\ACTISync.User."+sid,out var first);
   var ownPipe="ACTISync.User."+sid+"."+System.Diagnostics.Process.GetCurrentProcess().SessionId;
   if(!first) {
    if(invocation is null) return;
    using var pipe=UserProtocolPipe.Connect(ownPipe);
    using var writer=new StreamWriter(pipe){AutoFlush=true};
    writer.WriteLine(JsonSerializer.Serialize(invocation,Wire.Json)); return;
   }
   using var context=new HeadlessContext(ownPipe,invocation);
   Application.Run(context);
  } catch { /* Protocol and service errors are surfaced through the browser; never display agent UI. */ }
 }
}
