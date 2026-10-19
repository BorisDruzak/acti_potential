using System.Runtime.InteropServices;
using System.Text;

namespace ACTISync.Updater;

internal static class MsiIdentity {
 internal static void Verify(string path,VerifiedRelease release) {
  if(MsiOpenDatabase(path,IntPtr.Zero,out var database)!=0) throw new InvalidDataException("Invalid MSI database.");
  try {
   var expected=new Dictionary<string,string>{{"ProductName","ACTI Sync"},{"Manufacturer","ACTI"},{"UpgradeCode","{46C46CCD-DB5A-4A73-942C-4B3E8FDF8B0D}"},{"ProductVersion",release.Version},{"ACTISOURCESHA",release.SourceSha}};
   foreach(var property in expected) if(!string.Equals(Value(database,property.Key),property.Value,StringComparison.Ordinal)) throw new InvalidDataException("MSI product/source identity rejected.");
  } finally {MsiCloseHandle(database);}
 }
 private static string Value(uint db,string name) {
  if(MsiDatabaseOpenView(db,$"SELECT `Value` FROM `Property` WHERE `Property` = '{name}'",out var view)!=0) throw new InvalidDataException();
  try {if(MsiViewExecute(view,0)!=0 || MsiViewFetch(view,out var record)!=0) throw new InvalidDataException();try {var text=new StringBuilder(256);uint length=256;if(MsiRecordGetString(record,1,text,ref length)!=0)throw new InvalidDataException();return text.ToString();}finally{MsiCloseHandle(record);}}finally{MsiCloseHandle(view);}
 }
 [DllImport("msi.dll",EntryPoint="MsiOpenDatabaseW",CharSet=CharSet.Unicode)] private static extern uint MsiOpenDatabase(string path,IntPtr persist,out uint database);
 [DllImport("msi.dll",EntryPoint="MsiDatabaseOpenViewW",CharSet=CharSet.Unicode)] private static extern uint MsiDatabaseOpenView(uint database,string query,out uint view);
 [DllImport("msi.dll")]private static extern uint MsiViewExecute(uint view,uint record);
 [DllImport("msi.dll")]private static extern uint MsiViewFetch(uint view,out uint record);
 [DllImport("msi.dll",EntryPoint="MsiRecordGetStringW",CharSet=CharSet.Unicode)]private static extern uint MsiRecordGetString(uint record,uint field,StringBuilder value,ref uint length);
 [DllImport("msi.dll")]private static extern uint MsiCloseHandle(uint handle);
}
