using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ACTISync.Updater;

internal static class RolloutPolicy {
 internal const int MaximumBytes=2048;
 internal const string Capability="rollout-v1";
 internal static void RequireOrigin(Uri latest,Uri policy) {
  if(latest.Scheme!="https" || policy.Scheme!="https" || latest.GetLeftPart(UriPartial.Authority)!=policy.GetLeftPart(UriPartial.Authority))throw new InvalidDataException("Rollout origin rejected.");
 }
 internal static bool Allows(byte[] raw,string verifiedSha256,byte[] seed) {
  if(raw.Length>MaximumBytes)throw new InvalidDataException("Rollout policy exceeds bound.");
  try {
   using var document=JsonDocument.Parse(raw);var root=document.RootElement;
   if(root.ValueKind!=JsonValueKind.Object)throw new InvalidDataException("Invalid rollout policy.");
   var fields=root.EnumerateObject().Select(p=>p.Name).ToArray();
   if(fields.Length!=3 || fields.Distinct().Count()!=3 || fields.Except(new[]{"schema","sha256","percentage"}).Any() ||
    !root.GetProperty("schema").TryGetInt32(out var schema) || schema!=1 ||
    root.GetProperty("sha256").GetString()!=verifiedSha256 ||
    !root.GetProperty("percentage").TryGetInt32(out var percentage) || percentage<0 || percentage>100)throw new InvalidDataException("Rollout policy rejected.");
   return Bucket(seed,verifiedSha256)<percentage*100;
  }catch(Exception error)when(error is JsonException or InvalidOperationException or KeyNotFoundException){throw new InvalidDataException("Rollout policy rejected.");}
 }
 internal static int Bucket(byte[] seed,string sha256) {
  if(seed.Length!=32 || sha256.Length!=64 || sha256.Any(c=>!(c is >= '0' and <= '9' or >= 'a' and <= 'f')))throw new InvalidDataException("Invalid private cohort.");
  var digest=HMACSHA256.HashData(seed,Encoding.ASCII.GetBytes("ACTISync.rollout.v1:"+sha256));
  return (int)(BinaryPrimitives.ReadUInt64BigEndian(digest)%10000);
 }
 internal static byte[] LoadSeed(string path) {
  // Caller initializes the private SYSTEM/admin-only updater store first.
  // Never use machine/user identities, expose the seed, or replace corrupt state.
  var value=SecureStore.Read<CohortSeed>(path);
  if(value==null && File.Exists(path))throw new InvalidDataException("Invalid private cohort.");
  if(value==null){value=new CohortSeed{Seed=Convert.ToBase64String(RandomNumberGenerator.GetBytes(32))};SecureStore.Write(path,value);}
  try{var bytes=Convert.FromBase64String(value.Seed);if(bytes.Length!=32)throw new FormatException();return bytes;}
  catch(FormatException){throw new InvalidDataException("Invalid private cohort.");}
 }
 private sealed class CohortSeed {public string Seed {get;set;}="";}
}
