import { useEffect, useId, useRef, useState } from "react";
import { apiRequest, saveDownload } from "../api/client";
import type { FileVersion } from "../engine/schema";
import { useOfficeSyncStatus } from "../engine/useOfficeSyncStatus";
import { OfficeOpen } from "./OfficeOpen";
import "./office-sync.css";

export type OfficeSyncStatus = {
  file_id: string; current_file_id: string; filename: string; revision: number;
  can_edit: boolean; reason: string | null;
  state: "idle" | "opening" | "editing" | "local_saved" | "syncing" | "saved" | "offline" | "conflict" | "error" | "finished";
  session_id: string | null; last_seen_at: string | null; last_saved_at: string | null;
  lease_expires_at: string | null; editor: {id: string; display_name: string} | null;
};
type BinaryRevision = {file_id: string; revision: number; size: number; created_at: string; created_by_name?: string};
const labels: Record<OfficeSyncStatus["state"], string> = {
  idle: "", opening: "Открывается", editing: "Редактируется", local_saved: "Сохранено локально",
  syncing: "Синхронизируется", saved: "Сохранено в ACTI", offline: "Нет связи",
  conflict: "Конфликт: требуется действие", error: "Ошибка ACTI Sync", finished: "Сеанс завершён",
};
function date(value: string) {return new Date(value).toLocaleString("ru-RU");}

// The protocol carries only a short-lived one-use ticket, never an origin or credentials.
export function validSyncUri(uri: string): boolean {
  return /^acti-sync:\/\/launch\?ticket=[A-Za-z0-9_-]{43}$/.test(uri);
}

export function FileActions({file, mode, csrfToken, sync = true, showDownload = true, compact = false}: {
  file: FileVersion; mode: "editor" | "viewer"; csrfToken: string; sync?: boolean; showDownload?: boolean; compact?: boolean;
}) {
  const docx = file.name.toLowerCase().endsWith(".docx");
  const enabled = sync && docx;
  const {status, error: statusError, refresh} = useOfficeSyncStatus(file.id, mode, enabled);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [requested, setRequested] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<BinaryRevision[] | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [manualSaved, setManualSaved] = useState("");
  const versionInput = useRef<HTMLInputElement>(null);
  const versionInputId = useId();
  const lifecycle = useRef(0);
  useEffect(() => {
    lifecycle.current += 1;
    setError(""); setRequested(0); setBusy(false);
    setHistoryOpen(false); setHistory(null); setHistoryError("");
    setManualSaved("");
    return () => {lifecycle.current += 1;};
  }, [file.id, mode, enabled]);
  useEffect(() => {
    if (!historyOpen || !enabled) return;
    const abort = new AbortController();
    setHistoryError("");
    apiRequest<{items: BinaryRevision[]}>(`/office-sync/files/${file.id}/history?mode=${mode}`, {signal: abort.signal})
      .then(result => {if (!abort.signal.aborted) setHistory(result.items);})
      .catch(() => {if (!abort.signal.aborted) setHistoryError("Не удалось загрузить историю версий.");});
    return () => abort.abort();
  }, [file.id, mode, enabled, historyOpen, status?.revision]);

  async function launch() {
    if (busy || mode !== "editor" || !status?.can_edit || statusError) return;
    const generation = lifecycle.current;
    setBusy(true); setError("");
    try {
      const result = await apiRequest<{uri: string; session_id: string; expires_at: string}>("/office-sync/launch", {
        method: "POST", headers: {"X-CSRF-Token": csrfToken}, body: JSON.stringify({file_id: file.id}),
      });
      if (generation !== lifecycle.current) return;
      if (!validSyncUri(result.uri)) throw new Error("ACTI не выдал допустимую ссылку запуска. Повторите действие.");
      const link = document.createElement("a");
      link.href = result.uri; link.rel = "noreferrer"; link.click();
      // Opening a protocol is not proof of an installed client, Word, or a server commit.
      setRequested(Date.now()); refresh();
    } catch (failure) {
      if (generation === lifecycle.current) setError(failure instanceof Error ? failure.message : "Не удалось запустить ACTI Sync.");
    } finally {if (generation === lifecycle.current) setBusy(false);}
  }
  async function download(id: string, name: string) {
    setError("");
    try {await saveDownload(id, name, mode);} catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось скачать файл.");
    }
  }
  async function uploadVersion(selected: File) {
    if (busy || !status?.can_edit || mode !== "editor" || statusError) return;
    const generation = lifecycle.current;
    setError(""); setManualSaved("");
    if (!selected.name.toLowerCase().endsWith(".docx") || !selected.size || selected.size > 25 * 1024 * 1024) {
      setError("Выберите непустой DOCX размером не более 25 МиБ."); return;
    }
    setBusy(true);
    try {
      const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await selected.arrayBuffer())), byte => byte.toString(16).padStart(2, "0")).join("");
      if (generation !== lifecycle.current) return;
      const receipt = await apiRequest<{revision: number; sha256: string}>(`/office-sync/files/${file.id}/content`, {
        method: "PUT", body: selected, headers: {"Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "X-CSRF-Token": csrfToken, "If-Match": `"${status.revision}"`, "Idempotency-Key": crypto.randomUUID(), "X-Content-SHA256": sha256},
      });
      if (generation !== lifecycle.current) return;
      if (receipt.sha256 !== sha256 || !Number.isInteger(receipt.revision)) throw new Error("Подтверждение содержимого не получено. Проверьте актуальную редакцию перед повторной загрузкой.");
      setManualSaved(`Редакция ${receipt.revision} сохранена в ACTI`); refresh();
    } catch (failure) {
      if (generation === lifecycle.current) {setError(failure instanceof Error ? failure.message : "Не удалось загрузить новую версию."); refresh();}
    } finally {if (generation === lifecycle.current) setBusy(false);}
  }
  const current = {...file, id: status?.current_file_id || file.current_file_id || file.id, name: status?.filename || file.name};
  const lost = !!status?.session_id && status.state !== "finished" && status.state !== "opening" &&
    (!status.last_seen_at || Date.now() - Date.parse(status.last_seen_at) > 45000 ||
      !!status.lease_expires_at && Date.parse(status.lease_expires_at) <= Date.now());
  const unopened = !!requested && Date.now() - requested > 30000 && !status?.last_seen_at;
  const state = statusError ? "" : unopened ? "ACTI Sync не подключился. Проверьте установку и запуск программы." : lost ? "Нет связи — сохранение в ACTI не подтверждено" :
    status?.state === "saved" && !status.last_saved_at ? "Состояние сохранения неизвестно" : status ? labels[status.state] || "" : "";
  return <div className={`office-sync-actions${compact ? " office-sync-actions--compact" : ""}`}>
    <div className="office-sync-buttons">
      {enabled && mode === "editor" && status?.can_edit && <button type="button" className="primary" disabled={busy || !!statusError} onClick={() => void launch()}>Редактировать в Word</button>}
      {enabled && mode === "editor" && status?.can_edit && <>
        <button type="button" disabled={busy || !!statusError} onClick={() => versionInput.current?.click()}>Новая версия</button>
        <input ref={versionInput} id={versionInputId} aria-label={`Новая версия оригинала ${file.name}`} type="file" accept=".docx" hidden onChange={event => {
          const selected = event.target.files?.[0]; event.target.value = "";
          if (selected) void uploadVersion(selected);
        }}/>
      </>}
      <OfficeOpen file={current} mode={mode} csrfToken={csrfToken}/>
      {showDownload && <button type="button" onClick={() => void download(current.id, current.name)}>Скачать</button>}
    </div>
    {!enabled && <small>Версия {file.binary_revision || file.version}</small>}
    {enabled && <>
      <div className="office-sync-metadata">
      {status && <small>Актуальная редакция содержимого: {status.revision}</small>}
      {(state || statusError) && <small className="office-sync-state" role="status">{statusError || state}{status?.editor && !statusError ? ` · ${status.editor.display_name}` : ""}</small>}
      {status?.last_seen_at && <small>Последняя связь: {date(status.last_seen_at)}</small>}
      {status?.last_saved_at && <small>Последняя серверная синхронизация: {date(status.last_saved_at)}</small>}
      {mode === "editor" && status?.reason && !status.can_edit && <small>{status.reason}</small>}
      </div>
      {(!!requested || status?.state === "opening" || state === "Ошибка ACTI Sync" || lost) && (compact
        ? <small className="office-sync-help" role="status">Word не открылся? Проверьте установку и запуск ACTI Sync — инструкция ниже.</small>
        : <p className="office-sync-help" role="status">Подтвердите открытие программы в браузере. Если Word не открылся, установите или запустите ACTI Sync через администратора / GPO и проверьте наличие Word 2021. При ошибке откройте ACTI Sync в трее: повторите отправку или сохраните локальную копию. Для просмотра используйте Word, LibreOffice или скачайте файл.</p>)}
      <div className="office-sync-more">
      <details className="office-sync-help"><summary>Как редактировать в Word</summary><p>Нужны ACTI Sync и Microsoft Word 2021 на Windows. Установщик предоставляет администратор, в том числе через GPO. Подтвердите запрос браузера на запуск ACTI Sync. Сохраняйте в Word через Ctrl+S; «Сохранено в ACTI» появляется только после подтверждения сервера. При потере связи локальная копия остаётся в ACTI Sync.</p></details>
      <details onToggle={event => setHistoryOpen(event.currentTarget.open)}>
        <summary>История версий</summary>
        {historyError && <small role="alert">{historyError}</small>}
        {historyOpen && !history && !historyError && <small>Загрузка истории…</small>}
        {historyOpen && history?.map(item => <div className="office-sync-history" key={item.file_id}>
          <small>Редакция {item.revision} · {date(item.created_at)}{item.created_by_name ? ` · ${item.created_by_name}` : ""} · {(item.size / 1024).toFixed(1)} КБ</small>
          <button type="button" onClick={() => void download(item.file_id, current.name)}>Скачать редакцию {item.revision}</button>
        </div>)}
      </details>
      </div>
    </>}
    {error && <small role="alert">{error}</small>}
    {manualSaved && <small role="status">{manualSaved}</small>}
  </div>;
}
