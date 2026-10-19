using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace ACTISync.Core;

// Called by the interactive component only. The LocalService process never
// instantiates Word. A separate STA keeps its own message loop responsive.
public static class WordSourceConversion {
 public static async Task ConvertAsync(SessionView session) {
  using var cancel=new CancellationTokenSource();
  var completion=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
  var thread=new Thread(()=> {
   try { Convert(session,cancel.Token); completion.TrySetResult(); }
   catch(Exception error) { completion.TrySetException(error); }
  }) { IsBackground=true,Name="ACTI Word conversion" };
  thread.SetApartmentState(ApartmentState.STA); thread.Start();
  try { await completion.Task.WaitAsync(TimeSpan.FromSeconds(120)); }
  finally { cancel.Cancel(); }
 }
 private static void Convert(SessionView session,CancellationToken cancel) {
  var source=session.ConversionSourcePath ?? throw new IOException("Нет исходного файла конвертации.");
  var target=Path.GetFullPath(session.WorkingPath);
  var folder=Path.GetDirectoryName(target)!;
  if(!SafeFiles.WordSourceFormat(session.SourceFormat) || session.SourceFormat=="docx" ||
     !string.Equals(Path.GetFullPath(source),Path.Combine(folder,"original."+session.SourceFormat),StringComparison.OrdinalIgnoreCase) ||
     Path.GetFileName(target)!=SafeFiles.FileName(session.FileName) || session.ConversionSourceSha256?.Length!=64)
   throw new IOException("Недопустимый путь конвертации Word.");
  SafeFiles.NoReparse(source); SafeFiles.NoReparse(target);
  if(File.Exists(target)) { if(SafeFiles.ValidDocx(target)) return; throw new IOException("Рабочая копия уже существует; перезапись запрещена."); }
  // The verified handle denies writes/delete/replacement throughout COM open.
  using var input=SafeFiles.OpenRead(source);
  if(!SafeFiles.WordConversionInput(source,session.SourceFormat) || ConvertHash(input)!=session.ConversionSourceSha256)
   throw new IOException("Исходный файл изменён или повреждён.");
  var temporary=Path.Combine(folder,"converted-"+Guid.NewGuid().ToString("N")+".docx");
  dynamic? word=null,document=null,documents=null,options=null;
  var owned=false; int security=3; bool links=false;
  try {
   cancel.ThrowIfCancellationRequested();
   var type=Type.GetTypeFromProgID("Word.Application") ?? throw new IOException("Microsoft Word не найден.");
   word=Activator.CreateInstance(type) ?? throw new IOException("Microsoft Word не запущен.");
   documents=word.Documents;
   // Never close or reconfigure an instance containing somebody else's document.
   if((int)documents.Count!=0) throw new IOException("Не удалось создать отдельный экземпляр Word.");
   owned=true; security=(int)word.AutomationSecurity; word.AutomationSecurity=3; // msoAutomationSecurityForceDisable
   word.Visible=false; word.DisplayAlerts=0;
   options=word.Options; links=(bool)options.UpdateLinksAtOpen; options.UpdateLinksAtOpen=false;
   var unavailablePassword=Guid.NewGuid().ToString("N");
   document=documents.Open(FileName:source,ConfirmConversions:false,ReadOnly:true,AddToRecentFiles:false,
    PasswordDocument:unavailablePassword,PasswordTemplate:unavailablePassword,
    WritePasswordDocument:unavailablePassword,WritePasswordTemplate:unavailablePassword,
    Visible:false,OpenAndRepair:false,NoEncodingDialog:true);
   cancel.ThrowIfCancellationRequested();
   // 12=wdFormatXMLDocument: an explicit macro-free DOCX, independent of user's default format.
   document.SaveAs2(FileName:temporary,FileFormat:12,AddToRecentFiles:false,ReadOnlyRecommended:false);
   document.Close(SaveChanges:0); Marshal.FinalReleaseComObject(document); document=null;
   if(!SafeFiles.ValidDocx(temporary) || ConvertHash(input)!=session.ConversionSourceSha256)
    throw new IOException("Word не создал корректную рабочую копию DOCX.");
   cancel.ThrowIfCancellationRequested();
   SafeFiles.NoReparse(target); SafeFiles.NoReparse(temporary);
   using(var bytes=SafeFiles.OpenRead(temporary)) { }
   // Move is create-only. A failed/repeated conversion cannot overwrite user edits.
   File.Move(temporary,target,false);
  } finally {
   if(document!=null) { try { document.Close(SaveChanges:0); } catch { } Release(document); }
   if(word!=null && owned) {
    try { if(options!=null) options.UpdateLinksAtOpen=links; word!.AutomationSecurity=security; word.Quit(SaveChanges:0); } catch { }
   }
   Release(options); Release(documents); Release(word);
   if(File.Exists(temporary)) { try { SafeFiles.NoReparse(temporary); File.Delete(temporary); } catch(IOException) { } }
  }
 }
 private static string ConvertHash(FileStream stream) { stream.Position=0; return System.Convert.ToHexStringLower(SHA256.HashData(stream)); }
 private static void Release(object? value) { if(value!=null && Marshal.IsComObject(value)) Marshal.FinalReleaseComObject(value); }
}
