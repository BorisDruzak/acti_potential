import { useEffect, useRef, useState } from "react";
import { MicrosoftWordLogoIcon } from "@phosphor-icons/react/dist/csr/MicrosoftWordLogo";
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/csr/DownloadSimple";
import { apiRequest, saveDownload } from "../api/client";
import type { FileVersion } from "../engine/schema";
import { useOfficeSyncStatus } from "../engine/useOfficeSyncStatus";
import { discoverLocalBridge, postLocalTicket } from "../engine/localOfficeSync";
import "./office-sync.css";

export type OfficeSyncStatus = {
  file_id: string; current_file_id: string; filename: string; revision: number;
  can_edit: boolean; can_reopen?: boolean; reason: string | null;
  state: "idle" | "opening" | "editing" | "local_saved" | "syncing" | "saved" | "offline" | "conflict" | "error" | "finished";
  session_id: string | null; last_seen_at: string | null; last_saved_at: string | null;
  lease_expires_at: string | null; editor: {id: string; display_name: string} | null;
  document_open?: boolean | null; opened_at?: string | null; closed_at?: string | null;
};
type BinaryRevision = {file_id: string; filename?: string; revision: number; size: number; created_at: string; created_by_name?: string};
export const officeSyncLabels: Record<OfficeSyncStatus["state"], string> = {
  idle: "", opening: "Открывается", editing: "Редактируется", local_saved: "Сохранено локально",
  syncing: "Синхронизируется", saved: "Сохранено в ACTI", offline: "Нет связи",
  conflict: "Конфликт: требуется действие", error: "Ошибка ACTI Sync", finished: "Сеанс завершён",
};
export function validSyncUri(uri: string): boolean {
  return /^acti-sync:\/\/launch\?ticket=[A-Za-z0-9_-]{43}$/.test(uri);
}
export const isOfficeSyncSource = (name: string) => /\.(doc|docx|docm|dot|dotx|dotm|rtf|odt)$/i.test(name);

export function FileActions({file, mode, csrfToken, sync = true, showDownload = true, compact = false}: {
  file: FileVersion; mode: "editor" | "viewer"; csrfToken: string; sync?: boolean; showDownload?: boolean; compact?: boolean;
}) {
  const enabled = sync && isOfficeSyncSource(file.name);
  const {status, error: statusError, refresh} = useOfficeSyncStatus(file.id, mode, enabled);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    setError(""); setBusy(false);
    const cancel = () => pending.current?.abort();
    window.addEventListener("acti:unauthorized", cancel);
    return () => {cancel(); window.removeEventListener("acti:unauthorized", cancel);};
  }, [file.id, mode, enabled, csrfToken]);
  const canReopen = mode === "editor" && !!status?.can_reopen && !!status.session_id && !statusError && status.state !== "finished" &&
    !!status.last_seen_at && Date.now() - Date.parse(status.last_seen_at) <= 35000 && Date.now() - Date.parse(status.last_seen_at) >= -5000 &&
    !!status.lease_expires_at && Date.parse(status.lease_expires_at) > Date.now();
  async function edit() {
    if (pending.current && !pending.current.signal.aborted || busy || mode !== "editor" || statusError || !status?.can_edit && !canReopen) return;
    const active = new AbortController(); pending.current = active;
    setBusy(true); setError("");
    try {
      if (canReopen) {
        await apiRequest("/office-sync/agent/commands", {method: "POST", headers: {"X-CSRF-Token": csrfToken},
          body: JSON.stringify({session_id: status.session_id, action: "open"}), signal: active.signal});
      } else {
        const bridge = await discoverLocalBridge(active.signal);
        active.signal.throwIfAborted();
        const result = await apiRequest<{uri: string; expires_at: string}>("/office-sync/launch", {
          method: "POST", headers: {"X-CSRF-Token": csrfToken}, body: JSON.stringify({file_id: file.id}), signal: active.signal,
        });
        active.signal.throwIfAborted();
        if (!validSyncUri(result.uri) || !(Date.parse(result.expires_at) > Date.now())) throw new Error("invalid_launch_ticket");
        if (bridge) await postLocalTicket(bridge, "launch", result.uri, result.expires_at, active.signal);
        else {
          // This path is reachable only from the user's edit click, never from discovery.
          const link = document.createElement("a"); link.href = result.uri; link.rel = "noreferrer"; link.click();
        }
      }
      if (!active.signal.aborted) refresh();
    } catch {
      if (!active.signal.aborted) {setError("Не удалось открыть документ. Проверьте состояние в панели ACTI Sync и повторите действие."); refresh();}
    } finally {
      if (!active.signal.aborted) setBusy(false);
      if (pending.current === active) pending.current = null;
    }
  }
  async function download() {
    setError("");
    try {await saveDownload(status?.current_file_id || file.current_file_id || file.id, status?.filename || file.name, mode);}
    catch {setError("Не удалось скачать файл. Повторите действие.");}
  }
  return <div className={`office-sync-actions${compact ? " office-sync-actions--compact" : ""}`}>
    <div className="office-sync-buttons">
      {enabled && mode === "editor" && (status?.can_edit || canReopen) && <button type="button" className="primary" disabled={busy || !!statusError} onClick={() => void edit()}>{compact && <MicrosoftWordLogoIcon size={19} aria-hidden="true"/>}Редактировать в Word</button>}
      {showDownload && <button type="button" onClick={() => void download()}>{compact && <DownloadSimpleIcon size={18} aria-hidden="true"/>}Скачать</button>}
    </div>
    {error && <small role="alert">{error}</small>}
  </div>;
}

/** Version downloads live with document audit, rather than in each action row. */
export function FileRevisionHistory({file, mode}: {file: FileVersion; mode: "editor" | "viewer"}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<BinaryRevision[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setItems(null); setError("");
    if (!open) return;
    const active = new AbortController();
    apiRequest<{items: BinaryRevision[]}>(`/office-sync/files/${file.id}/history?mode=${mode}`, {signal: active.signal})
      .then(result => {if (!active.signal.aborted) setItems(result.items);})
      .catch(() => {if (!active.signal.aborted) setError("Не удалось загрузить историю версий.");});
    return () => active.abort();
  }, [file.id, mode, open]);
  async function download(item: BinaryRevision) {
    try {await saveDownload(item.file_id, item.filename || file.name, mode);}
    catch {setError("Не удалось скачать редакцию.");}
  }
  return <details onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{file.name}</summary>
    {open && !items && !error && <small>Загрузка истории…</small>}
    {open && items?.map(item => <div className="office-sync-history" key={item.file_id}>
      <small>Редакция {item.revision} · {new Date(item.created_at).toLocaleString("ru-RU")}{item.created_by_name ? ` · ${item.created_by_name}` : ""}</small>
      <button type="button" onClick={() => void download(item)}>Скачать редакцию {item.revision}</button>
    </div>)}
    {error && <small role="alert">{error}</small>}
  </details>;
}
