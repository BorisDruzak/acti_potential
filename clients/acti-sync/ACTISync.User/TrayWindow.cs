using ACTISync.Core;
using Microsoft.Win32;
using System.Diagnostics;

namespace ACTISync.User;

internal sealed class TrayWindow : Form {
 private readonly NotifyIcon tray;
 private readonly ListView list=new() { Dock=DockStyle.Fill,View=View.Details,FullRowSelect=true,MultiSelect=false,HideSelection=false };
 private readonly Label message=new() { Dock=DockStyle.Bottom,AutoSize=false,Height=55,Padding=new Padding(8),Text="ACTI Sync "+Wire.Version+" · Подтверждение сохранения появляется только после ответа ACTI." };
 private readonly System.Windows.Forms.Timer timer=new(){Interval=5000};
 private bool exiting; private bool refreshing; private readonly HashSet<string> opening=[]; private readonly Dictionary<string,string> errors=[];
 public TrayWindow() {
  Text="ACTI Sync "+Wire.Version; Size=new Size(1000,430); MinimumSize=new Size(760,320); StartPosition=FormStartPosition.CenterScreen;
  list.Columns.Add("Документ",300); list.Columns.Add("Состояние",220); list.Columns.Add("Редакция",75); list.Columns.Add("Подтверждено ACTI",180);
  Controls.Add(list); Controls.Add(message);
  var buttons=new FlowLayoutPanel { Dock=DockStyle.Bottom,Height=45,Padding=new Padding(5),AutoSize=false };
  void Button(string label,Action action) { var b=new Button { Text=label,AutoSize=true }; b.Click+=(_,_)=>action(); buttons.Controls.Add(b); }
  Button("Открыть локальную копию",()=>_=OpenSelected()); Button("Повторить",()=>_=Action("retry")); Button("Сохранить копию…",SaveCopy); Button("Завершить сеанс",()=>_=Action("finish"));
  Controls.Add(buttons);
  var menu=new ContextMenuStrip(); menu.Items.Add("Открыть ACTI Sync",null,(_,_)=>ShowWindow()); menu.Items.Add("Выход из трея (служба продолжит работу)",null,(_,_)=>{ exiting=true; Close(); });
  tray=new NotifyIcon { Text="ACTI Sync "+Wire.Version,Icon=SystemIcons.Application,ContextMenuStrip=menu,Visible=true }; tray.DoubleClick+=(_,_)=>ShowWindow();
  FormClosing+=(_,e)=>{ if(!exiting) { e.Cancel=true; Hide(); } };
  Shown+=(_,_)=>_=RefreshSessions(); timer.Tick+=(_,_)=>_=RefreshSessions(); timer.Start();
 }
 public void ShowWindow() { Show(); WindowState=FormWindowState.Normal; Activate(); }
 private SessionView? Selected => list.SelectedItems.Count==1 ? list.SelectedItems[0].Tag as SessionView : null;
 private static string State(string state)=>state switch { "opening"=>"Открывается", "editing"=>"Редактируется", "local_saved"=>"Сохранено локально", "syncing"=>"Синхронизируется", "saved"=>"Сохранено в ACTI", "offline"=>"Нет связи · копия сохранена", "conflict"=>"Конфликт · требуется действие", "reauth"=>"Требуется вход в ACTI", "error"=>"Ошибка · копия сохранена", "finished"=>"Сеанс завершён", _=>state };
 private void NotifyProblem(string text) { tray.BalloonTipTitle="ACTI Sync"; tray.BalloonTipText=text; tray.BalloonTipIcon=ToolTipIcon.Warning; tray.ShowBalloonTip(8000); }
 private async Task RefreshSessions() {
  if(refreshing) return; refreshing=true;
  try {
   var result=await ServiceClient.Send(new()); if(!result.Ok) throw new IOException();
   var selected=Selected?.SessionId; list.BeginUpdate(); list.Items.Clear();
   foreach(var s in result.Sessions) { var item=new ListViewItem(new[]{s.FileName,State(s.Status),s.Revision.ToString(),s.LastAck?.ToLocalTime().ToString("g")??"Ещё нет"}){Tag=s,ToolTipText=s.Error??""}; list.Items.Add(item); if(s.SessionId==selected) item.Selected=true;
    if(s.Error!=null && (!errors.TryGetValue(s.SessionId,out var previous)||previous!=s.Error)) { errors[s.SessionId]=s.Error; NotifyProblem(s.Error); }
   }
   list.EndUpdate(); tray.Icon=result.Sessions.Any(s=>s.Status is "error" or "conflict" or "reauth") ? SystemIcons.Warning : SystemIcons.Application;
   message.Text=Selected?.Error??"ACTI Sync "+Wire.Version+" · Служба работает. Последний ACK указан для каждого файла. Для повторной авторизации откройте ACTI в браузере.";
  } catch { message.Text="Служба ACTI Sync недоступна. Запустите службу или обратитесь к администратору. Локальные файлы остаются на диске."; tray.Icon=SystemIcons.Warning; }
  finally { refreshing=false; }
 }
 public async Task Launch(string ticket) {
  try { ShowWindow(); message.Text="Получение документа из ACTI…"; var result=await ServiceClient.Send(new(){Action="launch",Ticket=ticket}); if(!result.Ok || result.Session is null) { NotifyProblem(result.Error??"Служба не подтвердила запуск."); message.Text=result.Error; return; } if(result.Session.Status=="conflict") { NotifyProblem(result.Session.Error??"Конфликт редакции."); return; } await OpenWord(result.Session); await RefreshSessions(); }
  catch { NotifyProblem("Не удалось связаться со службой ACTI Sync. Проверьте установку и подключение."); }
 }
 private async Task Action(string action) { var s=Selected; if(s is null) return; try { var result=await ServiceClient.Send(new(){Action=action,SessionId=s.SessionId}); if(!result.Ok) NotifyProblem(result.Error??"Действие не выполнено."); await RefreshSessions(); } catch { NotifyProblem("Служба ACTI Sync недоступна."); } }
 private async Task OpenSelected() { var s=Selected; if(s!=null) await OpenWord(s); }
 private static string? WordPath() {
  foreach(var view in new[]{RegistryView.Registry64,RegistryView.Registry32}) {
   using var hklm=RegistryKey.OpenBaseKey(RegistryHive.LocalMachine,view); using var key=hklm.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE");
   var value=key?.GetValue(null) as string;
   if(value!=null && Path.IsPathFullyQualified(value) && File.Exists(value)) return value;
  }
  foreach(var folder in new[]{Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86)}) foreach(var suffix in new[]{@"Microsoft Office\root\Office16\WINWORD.EXE",@"Microsoft Office\Office16\WINWORD.EXE"}) { var path=Path.Combine(folder,suffix); if(File.Exists(path)) return path; }
  return null;
 }
 private async Task OpenWord(SessionView s) {
  if(!opening.Add(s.SessionId)) return;
  try {
   var exe=WordPath(); if(exe is null) { await ServiceClient.Send(new(){Action="word_missing",SessionId=s.SessionId}); NotifyProblem("Microsoft Word не установлен. Установите Word 2021; локальная копия сохранена."); return; }
   SafeFiles.NoReparse(s.WorkingPath); if(!File.Exists(s.WorkingPath) || !SafeFiles.ValidDocx(s.WorkingPath)) { NotifyProblem("Локальный DOCX ещё не загружен или занят. Повторите открытие через несколько секунд."); return; }
   var start=new ProcessStartInfo(exe){UseShellExecute=false}; start.ArgumentList.Add(s.WorkingPath); Process.Start(start)?.Dispose();
   // Word's owner file confirms it opened this local document; a successful process start alone is not an editing acknowledgement.
   for(var i=0;i<40;i++) { if(Directory.EnumerateFiles(Path.GetDirectoryName(s.WorkingPath)!,"~$*.docx").Any()) { await ServiceClient.Send(new(){Action="opened",SessionId=s.SessionId}); return; } await Task.Delay(500); }
   NotifyProblem("Word запущен, но открытие документа ещё не подтверждено. Проверьте окно Word; исходный файл сохранён локально.");
  } catch { NotifyProblem("Word не открыл файл. Локальная копия сохранена; проверьте установку Office."); }
  finally { opening.Remove(s.SessionId); }
 }
 private void SaveCopy() {
  var s=Selected; if(s is null) return;
  using var dialog=new SaveFileDialog{FileName=s.FileName,Filter="Документ Word (*.docx)|*.docx",AddExtension=true,DefaultExt="docx",OverwritePrompt=true};
  if(dialog.ShowDialog(this)!=DialogResult.OK) return;
  try { SafeFiles.NoReparse(s.WorkingPath); using var input=SafeFiles.OpenRead(s.WorkingPath); using var output=new FileStream(dialog.FileName,FileMode.Create,FileAccess.Write,FileShare.None,81920,FileOptions.WriteThrough); input.CopyTo(output); output.Flush(true); message.Text="Копия сохранена. Она не подтверждена сервером ACTI."; } catch { NotifyProblem("Не удалось сохранить копию. Закончите сохранение в Word и повторите."); }
 }
 protected override void Dispose(bool disposing) { if(disposing) { timer.Dispose(); tray.Visible=false; tray.Dispose(); } base.Dispose(disposing); }
}
