using ACTISync.Core;
using System.Diagnostics;
using System.Text.Json;

namespace ACTISync.User;

internal sealed class AgentTray : IDisposable {
 private readonly NotifyIcon tray;
 private readonly Icon icon;
 private readonly ContextMenuStrip menu=new();
 private readonly string url;
 internal AgentTray(Uri origin) {
  url=new Uri(origin,"/work?sync=1").AbsoluteUri;
  // A distinct ACTI mark; the user component creates no management window.
  using var bitmap=new Bitmap(32,32); using(var graphics=Graphics.FromImage(bitmap)) {
   graphics.Clear(Color.Transparent); using var background=new SolidBrush(Color.FromArgb(19,116,94)); graphics.FillEllipse(background,1,1,30,30);
   using var font=new Font(FontFamily.GenericSansSerif,20,FontStyle.Bold,GraphicsUnit.Pixel); using var brush=new SolidBrush(Color.White); graphics.DrawString("A",font,brush,6,3);
  }
  var handle=bitmap.GetHicon(); try { using var temporary=Icon.FromHandle(handle); icon=(Icon)temporary.Clone(); } finally { DestroyIcon(handle); }
  menu.Items.Add("Открыть ACTI",null,(_,_)=>Open()); menu.Items.Add(new ToolStripMenuItem("Версия "+Wire.Version){Enabled=false});
  tray=new NotifyIcon {Icon=icon,Text="ACTI Sync · проверка связи",ContextMenuStrip=menu,Visible=true}; tray.DoubleClick+=(_,_)=>Open();
 }
 private void Open() { try { Process.Start(new ProcessStartInfo(url){UseShellExecute=true}); } catch(System.ComponentModel.Win32Exception) { } }
 internal void Refresh(bool online,bool preparing) {
  var updating=preparing;
  try {
   var path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"ACTI","SyncUpdateStatus","status.json");
   if(File.Exists(path) && DateTime.UtcNow-File.GetLastWriteTimeUtc(path)<TimeSpan.FromMinutes(15)) {
    using var document=JsonDocument.Parse(File.ReadAllText(path));
    if(document.RootElement.TryGetProperty("state",out var state)) updating|=state.GetString() is "preparing" or "installing" or "updating" or "downloading" or "bootstrapping";
   }
  } catch(Exception e) when(e is IOException or UnauthorizedAccessException or JsonException) { }
  tray.Text="ACTI Sync · "+(updating?"обновление":online?"на связи":"нет связи");
 }
 public void Dispose() { tray.Visible=false; tray.Dispose(); menu.Dispose(); icon.Dispose(); }
 [System.Runtime.InteropServices.DllImport("user32.dll")] [return:System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)] private static extern bool DestroyIcon(IntPtr handle);
}
