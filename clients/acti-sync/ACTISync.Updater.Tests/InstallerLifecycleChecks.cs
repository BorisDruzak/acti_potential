using ACTISync.Updater;
using System.Security.AccessControl;
using System.Security.Principal;

internal static class InstallerLifecycleChecks {
 internal static async Task Run(string directory) {
  void Denied(Action action){try{action();}catch(UnauthorizedAccessException){return;}throw new Exception("MSI lifecycle accepted an unauthenticated image or ACL");}
  Denied(InstallerLifecycle.RequireStagedImage);
  var system=new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null);
  var users=new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid,null);
  var security=new DirectorySecurity();security.SetOwner(system);security.SetAccessRuleProtection(true,false);
  security.AddAccessRule(new FileSystemAccessRule(system,FileSystemRights.FullControl,AccessControlType.Allow));
  InstallerLifecycle.RequireAccess(security,true,true);
  security.AddAccessRule(new FileSystemAccessRule(users,FileSystemRights.ReadAndExecute,AccessControlType.Allow));
  InstallerLifecycle.RequireAccess(security,true,false);Denied(()=>InstallerLifecycle.RequireAccess(security,true,true));
  security.AddAccessRule(new FileSystemAccessRule(users,FileSystemRights.WriteData,AccessControlType.Allow));Denied(()=>InstallerLifecycle.RequireAccess(security,true,false));
  foreach(var genericMutation in new[]{0x10000000,0x40000000,0x02000000}) {
   var descriptor=new RawSecurityDescriptor(ControlFlags.DiscretionaryAclPresent|ControlFlags.DiscretionaryAclProtected,system,system,null,new RawAcl(2,1));
   descriptor.DiscretionaryAcl!.InsertAce(0,new CommonAce(AceFlags.None,AceQualifier.AccessAllowed,genericMutation,users,false,null));
   var descriptorBytes=new byte[descriptor.BinaryLength];descriptor.GetBinaryForm(descriptorBytes,0);
   var genericSecurity=new DirectorySecurity();genericSecurity.SetSecurityDescriptorBinaryForm(descriptorBytes);Denied(()=>InstallerLifecycle.RequireAccess(genericSecurity,true,false));
  }
  security=new DirectorySecurity();security.SetOwner(users);security.SetAccessRuleProtection(true,false);Denied(()=>InstallerLifecycle.RequireAccess(security,true,true));
  var nullDacl=new RawSecurityDescriptor(ControlFlags.DiscretionaryAclPresent,system,system,null,null);var binary=new byte[nullDacl.BinaryLength];nullDacl.GetBinaryForm(binary,0);
  var fileSecurity=new FileSecurity();fileSecurity.SetSecurityDescriptorBinaryForm(binary);Denied(()=>InstallerLifecycle.RequireAccess(fileSecurity,false,true));
  Console.WriteLine("PASS MSI lifecycle rejects foreign execution, foreign owners, public access, writable ancestors and null DACLs");
  var serviceStates=new Queue<(uint Type,uint State,uint Pid)>([(0x10,2,0),(0x10,4,42)]);var waits=0;
  await InstallerLifecycle.WaitForService(()=>serviceStates.Dequeue(),_=>{waits++;return Task.CompletedTask;},42,CancellationToken.None);
  if(waits!=1 || serviceStates.Count!=0)throw new Exception("MSI service authenticated before RUNNING or rejected pending PID zero");
  try{await InstallerLifecycle.WaitForService(()=>(0x10,4,43),_=>Task.CompletedTask,42,CancellationToken.None);throw new Exception("Foreign running SCM PID accepted");}catch(UnauthorizedAccessException){}
  Console.WriteLine("PASS MSI service waits through START_PENDING PID zero and authenticates exact RUNNING PID before IPC");
  var receipt=Path.Combine(directory,"msi-resume.json");
  var first=new UserProcess(11,101,"S-1-5-21-1",7,42);
  var later=new UserProcess(22,202,"S-1-5-21-2",8,43,"Tray");
  var snapshots=new Queue<List<UserProcess>>([ [first], [later], [] ]);
  var attempts=new List<UserProcess>();
  var flow=new InstallerSessionLifecycle(()=>SecureStore.Read<UpdateRequest>(receipt),r=>SecureStore.Write(receipt,r),()=>SecureStore.Delete(receipt),()=>snapshots.Dequeue(),(user,ct)=>{
   var durable=SecureStore.Read<UpdateRequest>(receipt)!;
   if(!durable.Users.Contains(user))throw new Exception("MSI drain ran before exact identity was durable");
   attempts.Add(user);return Task.FromResult(true);
  },_=>true);
  await flow.Prepare(CancellationToken.None);
  if(!attempts.SequenceEqual(new[]{first,later}) || !SecureStore.Read<UpdateRequest>(receipt)!.Users.SequenceEqual(new[]{first,later}))throw new Exception("New MSI session lost");
  Console.WriteLine("PASS MSI drain persists exact initial and newly logged-in sessions before any component exits");
  SecureStore.Delete(receipt);attempts.Clear();
  flow=new(()=>SecureStore.Read<UpdateRequest>(receipt),r=>SecureStore.Write(receipt,r),()=>SecureStore.Delete(receipt),()=>[first,later],(user,ct)=>{attempts.Add(user);return Task.FromResult(user!=later);},_=>false);
  try{await flow.Prepare(CancellationToken.None);throw new Exception("Busy MSI drain accepted");}catch(IOException){}
  if(!SecureStore.Read<UpdateRequest>(receipt)!.Users.SequenceEqual(new[]{first,later}) || !attempts.SequenceEqual(new[]{first,later}))throw new Exception("Partial MSI drain lost resume set");
  if(flow.Recover() || !File.Exists(receipt))throw new Exception("Failed MSI resume consumed receipt");
  var resumed=new List<UserProcess>();
  flow=new(()=>SecureStore.Read<UpdateRequest>(receipt),r=>SecureStore.Write(receipt,r),()=>SecureStore.Delete(receipt),()=>[],(_,_)=>throw new Exception("Resume drained a component"),users=>WindowsSessions.ResumeAll(users,user=>{resumed.Add(user);return true;}));
  if(!flow.Recover() || File.Exists(receipt) || !resumed.SequenceEqual(new[]{first,first with{Component="Tray"},later}))throw new Exception("MSI resume did not use role-aware WTS resume plan");
  if(!flow.Recover())throw new Exception("Missing MSI receipt was not a no-op");
  Console.WriteLine("PASS partial MSI drain retains durable receipt until all identity-bound WTS resumes succeed");
  SecureStore.Write(receipt,new UpdateRequest{Users=[first,later]});resumed.Clear();var uncommitted=true;var receiptReads=0;
  flow=new(()=>{receiptReads++;return SecureStore.Read<UpdateRequest>(receipt);},r=>SecureStore.Write(receipt,r),()=>SecureStore.Delete(receipt),()=>[],(_,_)=>throw new Exception("Recovery drained a component"),users=>WindowsSessions.ResumeAll(users,user=>{resumed.Add(user);return true;}),()=>uncommitted);
  if(flow.Recover() || receiptReads!=0 || resumed.Count!=0 || !File.Exists(receipt))throw new Exception("Uncommitted MSI launched components or consumed resume receipt");
  uncommitted=false;
  if(!flow.Recover() || File.Exists(receipt) || resumed.Count!=3)throw new Exception("Committed MSI did not release resume gate");
  SecureStore.Write(receipt,new UpdateRequest{Users=[first,later]});resumed.Clear();uncommitted=true;
  if(!flow.Recover(allowUncommitted:true) || File.Exists(receipt) || resumed.Count!=3)throw new Exception("Authenticated rollback could not resume restored old components");
  Console.WriteLine("PASS MSI commit gate preserves receipt without launch; completed commit and authenticated rollback release resume");
  var writes=0;var drains=0;flow=new(()=>null,_=>throw new IOException("durability unavailable"),()=>{},()=>[first],(_,_)=>{drains++;return Task.FromResult(true);},_=>true);
  try{await flow.Prepare(CancellationToken.None);throw new Exception("Failed persistence accepted");}catch(IOException){}
  if(drains!=0)throw new Exception("MSI drain preceded durable receipt");
  flow=new(()=>null,_=>writes++,()=>{},()=>[first],(_,ct)=>{ct.ThrowIfCancellationRequested();drains++;return Task.FromResult(true);},_=>true);
  using(var cancelled=new CancellationTokenSource()){cancelled.Cancel();try{await flow.Prepare(cancelled.Token);throw new Exception("Cancelled MSI drain accepted");}catch(OperationCanceledException){}}
  if(drains!=0)throw new Exception("Cancelled MSI drain touched component");
  Console.WriteLine("PASS MSI receipt write failure and cumulative cancellation never force component exit");
  var reads=0;drains=0;UpdateRequest? durable=null;
  flow=new(()=>durable,r=>durable=r,()=>durable=null,()=>{reads++;return [first with{Pid=reads}];},(_,_)=>{drains++;return Task.FromResult(true);},_=>true);
  try{await flow.Prepare(CancellationToken.None);throw new Exception("Unbounded session churn accepted");}catch(IOException){}
  if(reads!=5 || drains!=4 || durable?.Users.Count!=4)throw new Exception("MSI session resnapshot bound changed");
  Console.WriteLine("PASS MSI session churn defers after three bounded resnapshots and preserves every drained identity");
 }
}
