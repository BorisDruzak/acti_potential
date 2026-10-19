using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace ACTISync.User;

internal sealed class BridgePolicy(Uri origin) {
 private readonly object gate=new();
 private readonly Dictionary<string,DateTimeOffset> challenges=[];
 internal bool AcceptOrigin(string? value)=>string.Equals(value,origin.GetLeftPart(UriPartial.Authority),StringComparison.Ordinal);
 internal string Issue(DateTimeOffset now) {
  lock(gate) {
   foreach(var key in challenges.Where(p=>p.Value<=now).Select(p=>p.Key).ToArray()) challenges.Remove(key);
   if(challenges.Count>=128) challenges.Remove(challenges.Keys.First());
   var nonce=Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+','-').Replace('/','_');
   challenges[nonce]=now.AddSeconds(45); return nonce;
  }
 }
 internal bool Consume(string? challenge,string? ticket,DateTimeOffset now) {
  if(challenge is null || ticket is null || !Regex.IsMatch(challenge,@"\A[A-Za-z0-9_-]{43}\z") || !Regex.IsMatch(ticket,@"\A[A-Za-z0-9_-]{43}\z")) return false;
  lock(gate) return challenges.Remove(challenge,out var expires) && expires>now;
 }
}
