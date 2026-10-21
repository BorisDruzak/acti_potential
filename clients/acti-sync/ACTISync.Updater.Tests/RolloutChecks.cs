using ACTISync.Updater;
using System.Text;
using System.Security.Cryptography;

internal static class RolloutChecks {
 internal static async Task Run() {
  var hash=new string('b',64);var seed=Enumerable.Range(0,32).Select(i=>(byte)i).ToArray();
  byte[] Policy(int percentage)=>Encoding.UTF8.GetBytes($"{{\"schema\":1,\"sha256\":\"{hash}\",\"percentage\":{percentage}}}");
  if(RolloutPolicy.Allows(Policy(0),hash,seed) || !RolloutPolicy.Allows(Policy(100),hash,seed))throw new Exception("0/100 rollout boundary");
  foreach(var target in new[]{new Uri("http://feed.example/policy"),new Uri("https://other.example/policy"),new Uri("https://feed.example:444/policy")}) {
   try{RolloutPolicy.RequireOrigin(new Uri("https://feed.example/latest"),target);throw new Exception("Unsafe rollout origin accepted");}catch(InvalidDataException){}
  }
  RolloutPolicy.RequireOrigin(new Uri("https://feed.example/latest"),new Uri("https://feed.example/policy"));
  try{RolloutPolicy.Allows(new byte[RolloutPolicy.MaximumBytes+1],hash,seed);throw new Exception("Oversized policy accepted");}catch(InvalidDataException){}
  var bucket=RolloutPolicy.Bucket(seed,hash);
  if(bucket!=RolloutPolicy.Bucket(seed,hash) || bucket<0 || bucket>=10000)throw new Exception("Non-deterministic cohort");
  for(var percentage=1;percentage<100;percentage++)if(RolloutPolicy.Allows(Policy(percentage),hash,seed)!=(bucket<percentage*100))throw new Exception("Partial rollout mismatch");
  var outcomes=Enumerable.Range(0,1000).Select(i=>RolloutPolicy.Allows(Policy(37),hash,SHA256.HashData(BitConverter.GetBytes(i)))).ToArray();
  if(outcomes.Count(v=>v)<300 || outcomes.Count(v=>v)>440)throw new Exception("Cohort distribution");
  foreach(var invalid in new[]{"{}",Encoding.UTF8.GetString(Policy(-1)),Encoding.UTF8.GetString(Policy(101)),Encoding.UTF8.GetString(Policy(100)).Replace(hash,new string('a',64)),"{\"schema\":1,\"sha256\":\""+hash+"\",\"percentage\":true}",Encoding.UTF8.GetString(Policy(100)).Replace("\"schema\":1","\"schema\":1,\"schema\":1"),Encoding.UTF8.GetString(Policy(100)).Replace("100}","100,\"url\":\"evil\"}")}) {
   try{RolloutPolicy.Allows(Encoding.UTF8.GetBytes(invalid),hash,seed);throw new Exception("Invalid policy accepted");}catch(InvalidDataException){}
  }
  var directory=Path.Combine(Path.GetTempPath(),"ACTISyncCohort-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
  try{var path=Path.Combine(directory,"cohort.json");var first=RolloutPolicy.LoadSeed(path);if(first.Length!=32 || !first.SequenceEqual(RolloutPolicy.LoadSeed(path)))throw new Exception("Cohort not persisted");File.WriteAllText(path,"{}");try{RolloutPolicy.LoadSeed(path);throw new Exception("Corrupt cohort replaced");}catch(InvalidDataException){}}finally{Directory.Delete(directory,true);}
  await Task.CompletedTask;
  Console.WriteLine("PASS rollout 0/partial/100 deterministic private cohort and malformed policy fail-closed");
 }
}
