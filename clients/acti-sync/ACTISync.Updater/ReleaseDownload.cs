using ACTISync.Core;
using System.Net;
using System.Security.Cryptography;

namespace ACTISync.Updater;

public static class ReleaseDownload {
 public static HttpClient CreateClient()=>new(new SocketsHttpHandler{AllowAutoRedirect=false,AutomaticDecompression=DecompressionMethods.None,UseCookies=false,Credentials=null,ConnectTimeout=TimeSpan.FromSeconds(20),MaxResponseHeadersLength=32}){Timeout=TimeSpan.FromMinutes(15)};
 public static async Task<byte[]> ReadBounded(HttpClient client,Uri uri,int max,CancellationToken ct,TimeSpan? timeout=null) {
  using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(timeout??TimeSpan.FromSeconds(30));ct=deadline.Token;
  using var response=await client.GetAsync(uri,HttpCompletionOption.ResponseHeadersRead,ct);
  if(response.StatusCode!=HttpStatusCode.OK || response.Content.Headers.ContentLength>max) throw new InvalidDataException("Release feed rejected.");
  using var input=await response.Content.ReadAsStreamAsync(ct); using var output=new MemoryStream(); var buffer=new byte[4096]; int count;
  while((count=await input.ReadAsync(buffer,ct))>0) { if(output.Length+count>max) throw new InvalidDataException("Release feed exceeded limit."); output.Write(buffer,0,count); }
  return output.ToArray();
 }
 public static async Task Download(HttpClient client,VerifiedRelease release,string destination,CancellationToken ct,TimeSpan? timeout=null) {
  using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);deadline.CancelAfter(timeout??TimeSpan.FromMinutes(15));ct=deadline.Token;
  var uri=release.DownloadUri;
  for(var redirects=0;redirects<=4;redirects++) {
   using var response=await client.GetAsync(uri,HttpCompletionOption.ResponseHeadersRead,ct);
   if((int)response.StatusCode is 301 or 302 or 303 or 307 or 308) {
    var location=response.Headers.Location??throw new InvalidDataException("Missing asset redirect."); var next=location.IsAbsoluteUri?location:new Uri(uri,location);
    if(!ReleaseTrust.AllowedAssetRedirect(next)) throw new InvalidDataException("Unsafe asset redirect."); uri=next; continue;
   }
   if(response.StatusCode!=HttpStatusCode.OK || response.Content.Headers.ContentEncoding.Count!=0 || response.Content.Headers.ContentLength is long size && size!=release.Size) throw new InvalidDataException("Asset size rejected.");
   SafeFiles.NoReparse(destination);
   using(var output=new FileStream(destination,FileMode.CreateNew,FileAccess.Write,FileShare.None,81920,FileOptions.WriteThrough|FileOptions.Asynchronous)) {
    using var input=await response.Content.ReadAsStreamAsync(ct); using var hash=IncrementalHash.CreateHash(HashAlgorithmName.SHA256); var buffer=new byte[81920]; long total=0; int count;
    while((count=await input.ReadAsync(buffer,ct))>0) { total+=count; if(total>release.Size) throw new InvalidDataException("Asset exceeds signed size."); hash.AppendData(buffer,0,count); await output.WriteAsync(buffer.AsMemory(0,count),ct); }
    if(total!=release.Size || Convert.ToHexStringLower(hash.GetHashAndReset())!=release.Sha256) throw new InvalidDataException("Asset checksum rejected."); output.Flush(true);
   }
   return;
  }
  throw new InvalidDataException("Too many asset redirects.");
 }
 public static FileStream VerifyFile(VerifiedRelease release,string path) {
  SafeFiles.NoReparse(path); var stream=SafeFiles.OpenRead(path);
  try { if(stream.Length!=release.Size || Convert.ToHexStringLower(SHA256.HashData(stream))!=release.Sha256) throw new InvalidDataException("Staged package rejected."); stream.Position=0; return stream; } catch { stream.Dispose(); throw; }
 }
}
