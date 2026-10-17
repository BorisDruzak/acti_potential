import { useState } from "react";
import { apiRequest } from "../api/client";

const extensions = {
  word: ["doc", "docx", "rtf", "odt"],
  libreoffice: ["doc", "docx", "rtf", "odt", "pdf", "txt"],
};
const schemes = {word: "ms-word", libreoffice: "vnd.libreoffice.command"};

export function OfficeOpen({file, mode, csrfToken}: {
  file: {id: string; name: string}; mode: "editor" | "viewer"; csrfToken: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [launched, setLaunched] = useState(false);
  const suffix = file.name.split(".").pop()?.toLowerCase() || "";
  const available = (["word", "libreoffice"] as const).filter(app => extensions[app].includes(suffix));
  if (!available.length) return null;
  async function open(application: "word" | "libreoffice") {
    setBusy(true); setError(""); setLaunched(false);
    try {
      const result = await apiRequest<{uri: string; expires_at: string}>(`/files/${file.id}/office-open`, {
        method: "POST", headers: {"X-CSRF-Token": csrfToken}, body: JSON.stringify({application, mode}),
      });
      const prefix = `${schemes[application]}:ofv|u|`;
      if (!result.uri.startsWith(prefix)) throw new Error("Ссылка открытия недоступна. Повторите действие.");
      const target = new URL(result.uri.slice(prefix.length));
      if (target.origin !== window.location.origin || !target.pathname.startsWith("/api/v1/office/files/") || target.username || target.password)
        throw new Error("Ссылка открытия недоступна. Повторите действие.");
      const link = document.createElement("a");
      link.href = result.uri;
      link.rel = "noreferrer";
      link.click();
      setLaunched(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось открыть оригинал");
    } finally {setBusy(false);}
  }
  return <span className="office-open-actions">
    {available.map(application => <button type="button" key={application} disabled={busy}
      onClick={() => void open(application)}>Открыть в {application === "word" ? "Word" : "LibreOffice"}</button>)}
    {error && <small role="alert">{error}</small>}
    {launched && <small role="status">Разрешите браузеру открыть установленное приложение. Оригинал открывается для просмотра; изменения можно загрузить новой версией.</small>}
  </span>;
}
