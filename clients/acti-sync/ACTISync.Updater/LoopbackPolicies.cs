using ACTISync.Core;
using Microsoft.Win32;

namespace ACTISync.Updater;

internal static class LoopbackPolicies {
 private static string Receipt=>Path.Combine(SecureStore.Root,"browser-policy.json");
 internal sealed record OwnedEntry(string Key,string Slot,string Origin);
 internal static void Provision() {
  var origin=ClientConfig.Load().Origin.GetLeftPart(UriPartial.Authority);
  var owned=SecureStore.Read<List<OwnedEntry>>(Receipt)??[];
  foreach(var entry in owned.Where(e=>e.Origin!=origin).ToArray()){Remove(entry);owned.Remove(entry);}SecureStore.Write(Receipt,owned);
  foreach(var vendor in new[]{@"SOFTWARE\Policies\Google\Chrome\LoopbackNetworkAllowedForUrls",@"SOFTWARE\Policies\Microsoft\Edge\LoopbackNetworkAllowedForUrls"}) {
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
  if(entry.Key is not (@"SOFTWARE\Policies\Google\Chrome\LoopbackNetworkAllowedForUrls" or @"SOFTWARE\Policies\Microsoft\Edge\LoopbackNetworkAllowedForUrls") || !int.TryParse(entry.Slot,out var slot) || slot is <43127 or >=44127)return;
  using var key=Registry.LocalMachine.OpenSubKey(entry.Key,true);if(key?.GetValue(entry.Slot)as string==entry.Origin)key.DeleteValue(entry.Slot,false);
 }
}
