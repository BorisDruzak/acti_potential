using System;
using System.Collections.Generic;
using System.Net;

namespace ACTISync.InstallerActions
{
    internal enum ProbeFailure { None, InvalidUrl, Certificate, NameResolution, Connection, Timeout, InvalidServer, Unhealthy, TlsNegotiation }

    internal sealed class ProbeResult
    {
        internal ProbeResult(ProbeFailure failure, ProbeDiagnostics? diagnostics = null)
        {
            Failure = failure;
            Diagnostics = diagnostics?.Snapshot() ?? Array.Empty<string>();
        }
        internal ProbeFailure Failure { get; }
        internal bool Success => Failure == ProbeFailure.None;
        internal IReadOnlyList<string> Diagnostics { get; }
        internal string Message
        {
            get
            {
                switch (Failure)
                {
                    case ProbeFailure.None: return "Соединение с ACTI установлено. Сервер готов к работе.";
                    case ProbeFailure.InvalidUrl: return "Укажите имя сервера или адрес HTTPS без пути, параметров и учётных данных.";
                    case ProbeFailure.Certificate: return "Не удалось проверить сертификат HTTPS: проверьте имя сервера, срок действия и доверие сертификату на этом компьютере. Для службы ACTI Sync корневой сертификат должен быть доверенным для локального компьютера.";
                    case ProbeFailure.TlsNegotiation: return "Не удалось согласовать защищённое соединение TLS с сервером. Проверьте поддержку TLS на этом компьютере и настройку HTTPS на сервере.";
                    case ProbeFailure.NameResolution: return "Имя сервера не найдено. Проверьте адрес сервера, подключение к сети и DNS.";
                    case ProbeFailure.Connection: return "Сервер недоступен или отклонил соединение. Проверьте сеть, порт HTTPS и доступность сервера.";
                    case ProbeFailure.Timeout: return "Время проверки соединения истекло. Проверьте сеть и доступность сервера, затем повторите проверку.";
                    case ProbeFailure.Unhealthy: return "Сервер ACTI отвечает, но пока не готов к работе. Проверьте состояние базы данных, Redis и файлового хранилища на сервере.";
                    default: return "Адрес не подтвердил работу сервера ACTI. Ожидаются ответы API проверки состояния без перенаправлений. Проверьте адрес и настройку сервера.";
                }
            }
        }

        internal static ProbeFailure Classify(WebExceptionStatus status, bool certificateRejected = false)
        {
            switch (status)
            {
                case WebExceptionStatus.TrustFailure: return ProbeFailure.Certificate;
                case WebExceptionStatus.SecureChannelFailure: return certificateRejected ? ProbeFailure.Certificate : ProbeFailure.TlsNegotiation;
                case WebExceptionStatus.NameResolutionFailure:
                case WebExceptionStatus.ProxyNameResolutionFailure: return ProbeFailure.NameResolution;
                case WebExceptionStatus.Timeout:
                case WebExceptionStatus.RequestCanceled: return ProbeFailure.Timeout;
                case WebExceptionStatus.ProtocolError: return ProbeFailure.InvalidServer;
                default: return ProbeFailure.Connection;
            }
        }
    }
}
