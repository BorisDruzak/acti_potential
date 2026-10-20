using System;

namespace ACTISync.InstallerActions
{
    internal static class ServerOrigin
    {
        internal static bool TryNormalize(string? input, out string origin)
        {
            origin = string.Empty;
            if (string.IsNullOrWhiteSpace(input)) return false;
            foreach (var character in input!)
                if (char.IsControl(character)) return false;

            var value = input!.Trim();
            if (value.IndexOfAny(new[] { '\\', '?', '#', '@', '%' }) >= 0) return false;
            foreach (var character in value)
                if (char.IsWhiteSpace(character)) return false;

            if (value.IndexOf("://", StringComparison.Ordinal) < 0) value = "https://" + value;
            if (!value.StartsWith("https://", StringComparison.OrdinalIgnoreCase)) return false;
            var authority = value.Substring("https://".Length);
            var slash = authority.IndexOf('/');
            if (slash >= 0)
            {
                if (authority.Substring(slash) != "/") return false;
                authority = authority.Substring(0, slash);
            }
            if (authority.Length == 0 || authority.EndsWith(":", StringComparison.Ordinal)) return false;

            if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) ||
                uri.Scheme != Uri.UriSchemeHttps || uri.Host.Length == 0 ||
                uri.HostNameType == UriHostNameType.Unknown || uri.Port <= 0 ||
                uri.UserInfo.Length != 0) return false;
            try
            {
                var host = uri.IdnHost.ToLowerInvariant();
                if (uri.HostNameType == UriHostNameType.IPv6) host = "[" + host.Trim('[', ']') + "]";
                origin = "https://" + host + (uri.IsDefaultPort ? string.Empty : ":" + uri.Port);
                return true;
            }
            catch (UriFormatException)
            {
                return false;
            }
        }
    }
}
