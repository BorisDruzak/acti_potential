using ACTISync.Updater;
internal static class PostInstallChecks {
 internal static async Task Run() {
  var user=new UserProcess(1,1,"S-1-5-21-1",1,1);var tray=user with{Component="Tray"};
  if(WindowsSessions.ResumeReady([user],[user],_=>"launch"))throw new Exception("Healthy without resumed Tray");
  if(!WindowsSessions.ResumeReady([user],[user,tray],_=>"launch"))throw new Exception("Resumed pair rejected");
  if(!WindowsSessions.ResumeReady([user],[],_=>"complete"))throw new Exception("Logged-off session blocks health");
  if(WindowsSessions.ResumeReady([user],[],_=>"defer"))throw new Exception("Uncertain session marked healthy");
  if(WindowsSessions.ResumeReady([user],[user with{Sid="S-1-5-21-2"},tray],_=>"launch"))throw new Exception("Foreign SID accepted");
  var events=new List<string>();var request=new UpdateRequest{Phase="installed"};
  var failure=new IOException("synthetic service health failure");
  try {
   InstallerCompletion.Reconcile(request.Phase=="installed",()=>{events.Add("health");throw failure;},()=>{events.Add("resume");return true;},()=>events.Add("resumed"));
   throw new Exception("Unhealthy update claimed healthy");
  }catch(IOException actual) {if(!ReferenceEquals(actual,failure))throw;}
  if(!events.SequenceEqual(new[]{"health","resume","resumed"}) || request.Phase!="installed")throw new Exception("Health failure stranded drained components or lost retry receipt");
  events.Clear();InstallerCompletion.Reconcile(true,()=>events.Add("health"),()=>{events.Add("resume");return true;},()=>events.Add("resumed"));
  if(!events.SequenceEqual(new[]{"health","resume","resumed"}))throw new Exception("Healthy completion ordering");
  Console.WriteLine("PASS failed post-install health resumes drained users and preserves installed retry phase without claiming current");
  await Task.CompletedTask;Console.WriteLine("PASS post-install resume requires native pair or verified logged-off/reused session");
 }
}
