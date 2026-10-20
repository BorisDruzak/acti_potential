using System;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Xml;
using System.Xml.Linq;

namespace ACTISync.InstallerActions
{
    internal static class HealthPayload
    {
        private const int MaximumBytes = 16 * 1024;

        internal static ProbeFailure Validate(byte[] body, bool ready, int statusCode)
        {
            if (body.Length == 0 || body.Length > MaximumBytes || (statusCode != 200 && statusCode != 503))
                return ProbeFailure.InvalidServer;
            try
            {
                var quotas = new XmlDictionaryReaderQuotas
                {
                    MaxDepth = 8, MaxArrayLength = MaximumBytes, MaxStringContentLength = MaximumBytes,
                    MaxNameTableCharCount = MaximumBytes, MaxBytesPerRead = 4096,
                };
                using (var reader = JsonReaderWriterFactory.CreateJsonReader(body, quotas))
                {
                    var root = XDocument.Load(reader).Root;
                    if (root == null || (string?)root.Attribute("type") != "object") return ProbeFailure.InvalidServer;
                    var status = FindSingle(root, "status");
                    if (status == null || (string?)status.Attribute("type") != "string") return ProbeFailure.InvalidServer;
                    if (!ready)
                        return status.Value == "live" ?
                            (statusCode == 200 ? ProbeFailure.None : ProbeFailure.Unhealthy) : ProbeFailure.InvalidServer;

                    if (status.Value != "ready" && status.Value != "unavailable") return ProbeFailure.InvalidServer;
                    var checks = FindSingle(root, "checks");
                    if (checks == null || (string?)checks.Attribute("type") != "object") return ProbeFailure.InvalidServer;
                    var healthy = status.Value == "ready" && statusCode == 200;
                    foreach (var name in new[] { "database", "redis", "storage" })
                    {
                        var check = FindSingle(checks, name);
                        if (check == null || (string?)check.Attribute("type") != "boolean") return ProbeFailure.InvalidServer;
                        healthy &= check.Value == "true";
                    }
                    return healthy ? ProbeFailure.None : ProbeFailure.Unhealthy;
                }
            }
            catch (Exception error) when (error is SerializationException || error is XmlException || error is ArgumentException)
            {
                return ProbeFailure.InvalidServer;
            }
        }

        private static XElement? FindSingle(XElement parent, string name)
        {
            XElement? found = null;
            foreach (var element in parent.Elements(name))
            {
                if (found != null) return null;
                found = element;
            }
            return found;
        }

        internal static byte[] ReadBounded(Stream stream)
        {
            using (var result = new MemoryStream())
            {
                var buffer = new byte[1024];
                int count;
                while ((count = stream.Read(buffer, 0, buffer.Length)) != 0)
                {
                    if (result.Length + count > MaximumBytes) throw new InvalidDataException("Health response exceeds limit.");
                    result.Write(buffer, 0, count);
                }
                return result.ToArray();
            }
        }
    }
}
