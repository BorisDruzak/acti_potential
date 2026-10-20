using ACTISync.Core;
using Microsoft.Win32;

namespace ACTISync.Updater;

internal static class LoopbackPolicies {
 internal static IReadOnlyList<string> PolicyKeys {get;}=Array.AsReadOnly(new[]{@"SOFTWARE\Policies\Google\Chrome\LoopbackNetworkAllowedForUrls",@"SOFTWARE\Policies\Microsoft\Edge\LoopbackNetworkAllowedForUrls",@"SOFTWARE\Policies\YandexBrowser\LoopbackNetworkAllowedForUrls"});
 private static string Receipt=>Path.Combine(SecureStore.Root,"browser-policy.json");
 internal sealed record OwnedEntry(string Key,string Slot,string Origin);
 // An omitted port is a wildcard in browser policy URL patterns, even for HTTPS.
 internal static string PolicyOrigin(Uri origin)=>origin.GetLeftPart(UriPartial.Authority)+(origin.IsDefaultPort?":443":"");
 internal static void Provision() {
  var origin=PolicyOrigin(ClientConfig.Load().Origin);
  var owned=SecureStore.Read<List<OwnedEntry>>(Receipt)??[];
  foreach(var entry in owned.Where(e=>e.Origin!=origin).ToArray()){Remove(entry);owned.Remove(entry);}SecureStore.Write(Receipt,owned);
  foreach(var vendor in PolicyKeys) {
   using var policy=Registry.LocalMachine.CreateSubKey(vendor,true);
   if(policy.GetValueNames().Any(name=>string.Equals(policy.GetValue(name)as string,origin,StringComparison.OrdinalIgnoreCase)))continue;
   // Record ownership before mutation so interruption never loses cleanup.
   var slot=Enumerable.Range(43127,1000).Select(i=>i.ToString(System.Globalization.CultureInfo.InvariantCulture)).FirstOrDefault(i=>policy.GetValue(i)==null);
   if(slot==null)continue;owned.RemoveAll(e=>e.Key==vendor && e.Slot==slot);owned.Add(new(vendor,slot,origin));SecureStore.Write(Receipt,owned);policy.SetValue(slot,origin,RegistryValueKind.String);
  }
 }
 internal static void Cleanup() {foreach(var entry in SecureStore.Read<List<OwnedEntry>>(Receipt)??[])Remove(entry);SecureStore.Delete(Receipt);}
 private static void Remove(OwnedEntry entry) {
  // The receipt is SYSTEM protected, yet still constrain the allowable registry
  // boundary and compare the value before removing our exact contribution.
  if(!IsOwnedSlot(entry))return;
  using var key=Registry.LocalMachine.OpenSubKey(entry.Key,true);if(MayRemove(entry,key?.GetValue(entry.Slot)as string))key!.DeleteValue(entry.Slot,false);
 }
 private static bool IsOwnedSlot(OwnedEntry entry)=>PolicyKeys.Contains(entry.Key,StringComparer.Ordinal) && int.TryParse(entry.Slot,out var slot) && slot is >=43127 and <44127;
 internal static bool MayRemove(OwnedEntry entry,string? currentOrigin)=>IsOwnedSlot(entry) && currentOrigin==entry.Origin;
}
