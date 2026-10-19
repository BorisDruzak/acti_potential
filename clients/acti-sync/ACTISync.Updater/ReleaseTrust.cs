using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ACTISync.Updater;

public sealed record VerifiedRelease(string Version,string SourceSha,string Filename,string Sha256,long Size,string ReleaseTag) {
 public Uri DownloadUri=>new($"https://github.com/BorisDruzak/acti_potential/releases/download/{ReleaseTag}/{Filename}");
 public bool IsNewerThan(string current)=>System.Version.Parse(Version)>System.Version.Parse(current);
}
public static class ReleaseTrust {
 public const long MaxMsiSize=512L*1024*1024;
 public const int MaxEnvelopeSize=32768;
 private static readonly string[] Fields=["schema","product","version","source_sha","filename","sha256","size","release_tag","published_at"];
 public static string PublicKey() { using var input=typeof(ReleaseTrust).Assembly.GetManifestResourceStream("ACTISync.ReleasePublicKey.pem")??throw new InvalidDataException(); using var reader=new StreamReader(input); return reader.ReadToEnd(); }
 internal static void Exact(JsonElement obj,params string[] fields) { if(obj.ValueKind!=JsonValueKind.Object) throw new InvalidDataException("Invalid release object."); var keys=obj.EnumerateObject().Select(p=>p.Name).ToArray(); if(keys.Length!=fields.Length || keys.Distinct(StringComparer.Ordinal).Count()!=keys.Length || fields.Any(f=>!keys.Contains(f,StringComparer.Ordinal))) throw new InvalidDataException("Unexpected release fields."); }
 private static string Text(JsonElement root,string name)=>root.GetProperty(name).ValueKind==JsonValueKind.String ? root.GetProperty(name).GetString()! : throw new InvalidDataException("Invalid release field.");
 public static VerifiedRelease Verify(ReadOnlyMemory<byte> envelope,string publicKey) {
  if(envelope.Length is <1 or >MaxEnvelopeSize) throw new InvalidDataException("Release envelope is too large.");
  using var json=JsonDocument.Parse(envelope,new JsonDocumentOptions{MaxDepth=8}); var root=json.RootElement; Exact(root,"payload","signature");
  var payload=Convert.FromBase64String(Text(root,"payload")); var signature=Convert.FromBase64String(Text(root,"signature"));
  using var rsa=RSA.Create(); rsa.ImportFromPem(publicKey);
  if(rsa.KeySize<3072 || !rsa.VerifyData(payload,signature,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1)) throw new CryptographicException("Release signature rejected.");
  using var signed=JsonDocument.Parse(payload,new JsonDocumentOptions{MaxDepth=8}); var data=signed.RootElement; Exact(data,Fields);
  if(!data.GetProperty("schema").TryGetInt32(out var schema) || schema!=1 || Text(data,"product")!="ACTISync") throw new InvalidDataException("Unsupported release.");
  var version=Text(data,"version");
  if(!Regex.IsMatch(version,@"\A1\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\z",RegexOptions.CultureInvariant) || !System.Version.TryParse(version,out var parsed) || parsed.Minor>255 || parsed.Build>65535) throw new InvalidDataException("Unsupported version.");
  var sha=Text(data,"source_sha"); var hash=Text(data,"sha256"); var filename=Text(data,"filename"); var tag=Text(data,"release_tag");
  if(!Regex.IsMatch(sha,@"\A[0-9a-f]{40}\z",RegexOptions.CultureInvariant) || !Regex.IsMatch(hash,@"\A[0-9a-f]{64}\z",RegexOptions.CultureInvariant) || filename!=$"ACTISync-{version}-win-x64.msi" || tag!=$"acti-sync-v{version}-{sha[..12]}") throw new InvalidDataException("Release identity rejected.");
  if(!data.GetProperty("size").TryGetInt64(out var size) || size is <1 or >MaxMsiSize) throw new InvalidDataException("Release size rejected.");
  var published=Text(data,"published_at"); if(!DateTimeOffset.TryParse(published,CultureInfo.InvariantCulture,DateTimeStyles.None,out var stamp) || stamp.Offset!=TimeSpan.Zero || stamp>DateTimeOffset.UtcNow.AddMinutes(10) || !published.EndsWith('Z')) throw new InvalidDataException("Release timestamp rejected.");
  return new(version,sha,filename,hash,size,tag);
 }
 public static bool AllowedAssetRedirect(Uri uri)=>uri.IsAbsoluteUri && uri.Scheme=="https" && uri.IsDefaultPort && uri.UserInfo.Length==0 && uri.Fragment.Length==0 && uri.Host is "release-assets.githubusercontent.com" or "objects.githubusercontent.com" or "github-releases.githubusercontent.com";
}
