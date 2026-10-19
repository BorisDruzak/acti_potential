using ACTISync.Core;
using System.Security.Cryptography;

namespace ACTISync.Updater;

// Windows Installer must replace the SCM executable without replacing the
// running installation helper. The sole helper image is a verified copy of the
// installed updater in a fixed SYSTEM-only directory, never an IPC-supplied path.
internal static class HelperImage {
 private static string ProofPath=>Path.Combine(SecureStore.Root,"helper-image.json");
 internal sealed record Proof(string Sha256,string Version);
 internal static void Stage() {
  var hash=CopyVerified(SecureStore.UpdaterExe,SecureStore.HelperExe);
  SecureStore.Write(ProofPath,new Proof(hash,SecureStore.CurrentVersion));
 }
 internal static void Verify() {
  var proof=SecureStore.Read<Proof>(ProofPath)??throw new InvalidDataException("Missing helper image receipt.");
  using var image=SafeFiles.OpenRead(SecureStore.HelperExe);
  if(proof.Version!=SecureStore.CurrentVersion || image.Length is <1 or >256L*1024*1024 || Convert.ToHexStringLower(SHA256.HashData(image))!=proof.Sha256)throw new InvalidDataException("Helper image receipt rejected.");
 }
 internal static string CopyVerified(string source,string destination) {
  SafeFiles.NoReparse(source);SafeFiles.NoReparse(destination);
  using var input=SafeFiles.OpenRead(source);if(input.Length is <1 or >256L*1024*1024)throw new InvalidDataException("Installed updater image size rejected.");
  var temporary=Path.Combine(Path.GetDirectoryName(destination)!,Guid.NewGuid().ToString("N")+".new");
  try {
   using(var output=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None,81920,FileOptions.WriteThrough)) {input.CopyTo(output);output.Flush(true);}
   input.Position=0;var hash=Convert.ToHexStringLower(SHA256.HashData(input));
   using(var copied=SafeFiles.OpenRead(temporary))if(copied.Length!=input.Length || Convert.ToHexStringLower(SHA256.HashData(copied))!=hash)throw new InvalidDataException("Helper copy rejected.");
   if(File.Exists(destination)){using var previous=SafeFiles.OpenRead(destination);}
   File.Move(temporary,destination,true);
   using(var ready=SafeFiles.OpenRead(destination))if(Convert.ToHexStringLower(SHA256.HashData(ready))!=hash)throw new InvalidDataException("Published helper copy rejected.");
   return hash;
  }finally{if(File.Exists(temporary))SecureStore.Delete(temporary);}
 }
}
