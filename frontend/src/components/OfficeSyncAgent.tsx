import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiRequest } from "../api/client";
import { DocumentDialog } from "./DocumentDialog";
import { officeSyncLabels, type OfficeSyncStatus } from "./OfficeSync";
import "./office-sync.css";

type AgentAction = "open" | "retry" | "finish" | "reveal";
type AgentCommand = {id: string; action: AgentAction; state: "pending" | "succeeded" | "failed" | "expired"; error?: string | null};
type Connection = {connection_id: string; online: boolean; last_seen_at: string | null; client_version: string | null};
type AgentDocument = OfficeSyncStatus & {
  document_id: string; document_title: string; can_control: boolean; connection_id: string | null;
  document_open: boolean | null; opened_at: string | null; closed_at: string | null; command: AgentCommand | null;
};
export type AgentStatus = {
  online: boolean; last_seen_at: string | null; client_version: string | null;
  connections: Connection[]; documents: AgentDocument[];
};
type Snapshot = {data: AgentStatus | null; error: string; denied: boolean; receivedAt: number | null};
type Notice = {id: number; text: string; expires: number};
const empty: Snapshot = {data: null, error: "", denied: false, receivedAt: null};
const actionNames: Record<AgentAction, string> = {open: "Открыть в Word", retry: "Повторить отправку", finish: "Завершить сеанс", reveal: "Открыть папку"};
const commandNames: Record<AgentCommand["state"], string> = {pending: "ожидает выполнения", succeeded: "выполнено", failed: "ошибка выполнения", expired: "время ожидания истекло"};
const fresh = (value: string | null, now: number) => !!value && Number.isFinite(Date.parse(value)) && now - Date.parse(value) <= 35000 && now - Date.parse(value) >= -5000;
const date = (value: string) => new Date(value).toLocaleString("ru-RU");
export const validAgentConnectUri = (uri: string) => /^acti-sync:\/\/connect\?ticket=[A-Za-z0-9_-]{43}$/.test(uri);

function useAgentStatus(owner: string) {
  const [snapshot, setSnapshot] = useState<Snapshot & {owner: string}>(() => ({...empty, owner}));
  const refreshRef = useRef(() => {});
  const denyRef = useRef(() => {});
  const refresh = useCallback(() => refreshRef.current(), []);
  const deny = useCallback(() => denyRef.current(), []);
  useEffect(() => {
    setSnapshot({...empty, owner});
    if (!owner) return;
    let stopped = false;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = () => {stopped = true; controller?.abort(); clearTimeout(timer);};
    const denied = () => {stop(); setSnapshot({data: null, error: "Сеанс браузера завершён или доступ отозван.", denied: true, receivedAt: null, owner});};
    async function load() {
      if (stopped) return;
      clearTimeout(timer); controller?.abort();
      const active = new AbortController(); controller = active;
      try {
        const data = await apiRequest<AgentStatus>("/office-sync/agent/status", {signal: active.signal});
        if (!data || typeof data.online !== "boolean" || !Array.isArray(data.connections) || !Array.isArray(data.documents)) throw new Error("invalid_agent_status");
        if (!active.signal.aborted && !stopped) setSnapshot({data, error: "", denied: false, receivedAt: Date.now(), owner});
      } catch (failure) {
        if (active.signal.aborted || stopped) return;
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) denied();
        else setSnapshot(previous => ({...previous, error: "Нет связи с ACTI. Состояние агента неизвестно."}));
      } finally {
        if (!stopped && !active.signal.aborted) timer = setTimeout(() => void load(), 7000);
      }
    }
    refreshRef.current = () => void load(); denyRef.current = denied;
    window.addEventListener("acti:unauthorized", denied);
    void load();
    return () => {stop(); refreshRef.current = () => {}; denyRef.current = () => {}; window.removeEventListener("acti:unauthorized", denied);};
  }, [owner]);
  const recordCommand = (sessionId: string, command: AgentCommand) => setSnapshot(previous => ({...previous,
    data: previous.data ? {...previous.data, documents: previous.data.documents.map(item => item.session_id === sessionId ? {...item, command} : item)} : null,
  }));
  return {...(snapshot.owner === owner ? snapshot : empty), refresh, deny, recordCommand};
}

/** This component owns no document form state and never changes workspace navigation. */
export function OfficeSyncAgent({owner, csrfToken, canControl}: {owner: string; csrfToken: string; canControl: boolean}) {
  const identity = owner ? `${owner}:${csrfToken}` : "";
  const {data, error: statusError, denied, receivedAt, refresh, deny, recordCommand} = useAgentStatus(identity);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [requested, setRequested] = useState(false);
  const [heartbeatTick, setHeartbeatTick] = useState(0);
  const [notices, setNotices] = useState<Notice[]>([]);
  const observations = useRef<{receivedAt: number | null; documents: Map<string, AgentDocument>}>({receivedAt: null, documents: new Map()});
  const noticeSequence = useRef(0);
  const now = Date.now();
  const lifecycle = useRef(0);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    lifecycle.current += 1; setOpen(false); setBusy(""); setError(""); setRequested(false); setNotices([]);
    observations.current = {receivedAt: null, documents: new Map()};
    return () => {lifecycle.current += 1; pending.current?.abort();};
  }, [identity]);
  useEffect(() => {
    if (denied) {pending.current?.abort(); setBusy(""); setError(""); setRequested(false); setNotices([]); observations.current = {receivedAt: null, documents: new Map()};}
  }, [denied]);
  // A pending HTTP request must not keep an expired heartbeat looking online.
  useEffect(() => {
    const times = [data?.last_seen_at, ...data?.connections.map(item => item.last_seen_at) || [], ...data?.documents.map(item => item.last_seen_at) || []]
      .filter((value): value is string => !!value).map(value => Date.parse(value) + 35001).filter(value => value > Date.now());
    times.push(...data?.documents.map(item => Date.parse(item.lease_expires_at || "")).filter(value => value > Date.now()) || []);
    if (!times.length) return;
    const timer = setTimeout(() => setHeartbeatTick(value => value + 1), Math.min(...times) - Date.now());
    return () => clearTimeout(timer);
  }, [data, heartbeatTick]);
  const online = !statusError && !!data?.online && fresh(data.last_seen_at, now);
  const connectionOnline = (id: string | null) => !statusError && !!id && !!data?.connections.some(item => item.connection_id === id && item.online && fresh(item.last_seen_at, now));
  const editingCount = data?.documents.filter(item => online && connectionOnline(item.connection_id) && fresh(item.last_seen_at, now) &&
    item.document_open === true && item.state !== "finished" && !!item.lease_expires_at && Date.parse(item.lease_expires_at) > now).length || 0;
  useEffect(() => {
    if (!data || statusError || denied || receivedAt === null) return;
    const observed = observations.current;
    // Command acceptance changes panel data, but is not another server observation.
    if (receivedAt === observed.receivedAt) return;
    const cutoff = observed.receivedAt;
    const newEvent = (value: string | null, previous: string | null | undefined) => !!value && value !== previous && cutoff !== null &&
      Date.parse(value) >= cutoff && Date.parse(value) <= receivedAt + 5000;
    const messages: string[] = [];
    for (const item of data.documents) {
      if (!item.session_id || !fresh(item.last_seen_at, receivedAt) || !data.connections.some(connection =>
        connection.connection_id === item.connection_id && connection.online && fresh(connection.last_seen_at, receivedAt))) continue;
      const previous = observed.documents.get(item.session_id);
      const title = item.document_title || item.filename || "Документ";
      if (cutoff !== null) {
        const opened = newEvent(item.opened_at, previous?.opened_at);
        const closed = newEvent(item.closed_at, previous?.closed_at);
        if (opened || previous && item.document_open === true && previous.document_open !== true) messages.push(`${title}: открыт в Word`);
        if (closed || previous?.document_open === true && item.document_open === false) messages.push(`${title}: закрыт в Word`);
        if (previous && previous.state !== item.state) {
          if (item.state === "local_saved") messages.push(`${title}: сохранено локально, ожидается подтверждение ACTI`);
          else if (item.state === "syncing") messages.push(`${title}: синхронизируется с ACTI`);
          else if (item.state === "conflict") messages.push(`${title}: конфликт, требуется действие в ACTI Sync`);
          else if (item.state === "error") messages.push(`${title}: ошибка синхронизации, проверьте ACTI Sync`);
        }
        if (newEvent(item.last_saved_at, previous?.last_saved_at))
          messages.push(`${title}: редакция ${item.revision} сохранена в ACTI`);
      }
      observed.documents.set(item.session_id, item);
    }
    observed.receivedAt = receivedAt;
    if (messages.length) setNotices(previous => [...previous.filter(item => item.expires > receivedAt),
      ...messages.map(text => ({id: ++noticeSequence.current, text, expires: receivedAt + 10000}))].slice(-3));
  }, [data, statusError, denied, receivedAt]);
  useEffect(() => {
    if (!notices.length) return;
    const timer = setTimeout(() => setNotices(previous => previous.filter(item => item.expires > Date.now())), Math.max(0, Math.min(...notices.map(item => item.expires)) - Date.now()));
    return () => clearTimeout(timer);
  }, [notices]);
  async function connect() {
    if (busy || !canControl || denied) return;
    const generation = lifecycle.current;
    const active = new AbortController(); pending.current = active;
    setBusy("connect"); setError("");
    try {
      const result = await apiRequest<{uri: string; expires_at: string}>("/office-sync/agent/connect", {
        method: "POST", headers: {"X-CSRF-Token": csrfToken}, signal: active.signal,
      });
      if (active.signal.aborted || generation !== lifecycle.current) return;
      if (!validAgentConnectUri(result.uri) || !(Date.parse(result.expires_at) > Date.now())) throw new Error("invalid_connect_uri");
      const link = document.createElement("a"); link.href = result.uri; link.rel = "noreferrer"; link.click();
      setRequested(true); refresh();
    } catch (failure) {
      if (active.signal.aborted || generation !== lifecycle.current) return;
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) deny();
      else setError("Не удалось подключить агент. Проверьте установку ACTI Sync и повторите действие.");
    } finally {if (!active.signal.aborted && generation === lifecycle.current) setBusy("");}
  }
  async function command(item: AgentDocument, action: AgentAction) {
    if (busy || denied || !canControl || !item.can_control || !item.session_id || !connectionOnline(item.connection_id)) return;
    const generation = lifecycle.current;
    const active = new AbortController(); pending.current = active;
    setBusy(item.session_id); setError("");
    try {
      const result = await apiRequest<{id: string; state: "pending"}>("/office-sync/agent/commands", {
        method: "POST", headers: {"X-CSRF-Token": csrfToken}, body: JSON.stringify({session_id: item.session_id, action}), signal: active.signal,
      });
      if (active.signal.aborted || generation !== lifecycle.current) return;
      recordCommand(item.session_id, {...result, action});
    } catch (failure) {
      if (active.signal.aborted || generation !== lifecycle.current) return;
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) deny();
      else {setError("Команда не принята. Обновите состояние и повторите действие."); refresh();}
    } finally {if (!active.signal.aborted && generation === lifecycle.current) setBusy("");}
  }
  const presence = online ? "на связи" : !data && !statusError ? "проверка связи" : "нет связи";
  return <>
    <button type="button" className={`office-agent-trigger${online ? " office-agent-trigger--online" : ""}`} aria-label={`ACTI Sync: ${presence}${editingCount ? ` · редактируется документов: ${editingCount}` : ""}`}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
      <span className="office-agent-dot" aria-hidden="true"/><span>ACTI Sync</span><span className="office-agent-presence">{presence}</span>
      {editingCount > 0 && <span className="office-agent-editing" title={`Редактируется документов: ${editingCount}`} aria-hidden="true"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m15 5 4 4M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15l-1 5Z"/></svg><span>{editingCount}</span></span>}
    </button>
    {notices.length > 0 && <div className="office-agent-notices" role="region" aria-label="Уведомления ACTI Sync" aria-live="polite" aria-relevant="additions">
      {notices.map(item => <div className="office-agent-notice" key={item.id}><span>{item.text}</span><button type="button" aria-label="Скрыть уведомление" onClick={() => setNotices(previous => previous.filter(notice => notice.id !== item.id))}>×</button></div>)}
    </div>}
    {open && <DocumentDialog label="ACTI Sync" className="office-agent-panel" onClose={() => setOpen(false)}>
      <div className="toolbar"><h2>ACTI Sync</h2><button type="button" autoFocus onClick={() => setOpen(false)}>Закрыть</button></div>
      <div className="office-agent-summary">
        <strong role="status">{online ? "Агент на связи" : presence === "проверка связи" ? "Проверяем связь с агентом…" : "Агент не на связи"}</strong>
        {data?.client_version && <small>Версия агента: {data.client_version}</small>}
        {data?.last_seen_at && <small>Последняя связь агента: {date(data.last_seen_at)}</small>}
        {statusError && <p role="alert">{statusError}</p>}
        <div className="button-row">
          {canControl && <button type="button" disabled={!!busy || denied} onClick={() => void connect()}>{busy === "connect" ? "Подключаем…" : "Подключить агент"}</button>}
          <button type="button" disabled={denied} onClick={refresh}>Обновить состояние</button>
        </div>
        {requested && !online && <small role="status">Ожидаем подтверждение связи от агента. Подтвердите открытие программы в браузере.</small>}
      </div>
      <p className="office-sync-help">ACTI Sync работает в фоне. Установите один MSI через администратора или GPO. Редактируйте документ в Word и сохраняйте через Ctrl+S; управление сеансами доступно здесь. «Сохранено в ACTI» появляется после подтверждения сервера.</p>
      <h3>Документы и сеансы</h3>
      {data && !data.documents.length && <p className="office-sync-help">Открытых сеансов редактирования нет.</p>}
      <ul className="office-agent-documents">
        {data?.documents.map(item => {
          const connected = connectionOnline(item.connection_id);
          const known = connected && fresh(item.last_seen_at, now);
          const documentState = item.document_open === true ? "Открыт в Word" : item.document_open === false ? "Закрыт в Word" : "Состояние Word неизвестно";
          const syncState = item.state === "saved" && !item.last_saved_at ? "Сохранение не подтверждено" : officeSyncLabels[item.state];
          return <li key={item.session_id || item.file_id}>
            <strong>{item.document_title || "Документ"}</strong><span>{item.filename}</span>
            <div className="office-agent-document-status"><span>{known || item.document_open === null ? documentState : `Последнее известное состояние: ${documentState.charAt(0).toLowerCase()}${documentState.slice(1)}`}</span>
              <span>{known || item.state === "finished" ? syncState : "Нет свежего подтверждения синхронизации"}</span></div>
            <small>Актуальная редакция содержимого: {item.revision}</small>
            {item.opened_at && <small>Открыт: {date(item.opened_at)}</small>}
            {item.closed_at && <small>Закрыт: {date(item.closed_at)}</small>}
            {item.last_saved_at && <small>Последняя серверная синхронизация: {date(item.last_saved_at)}</small>}
            {item.command && <small role="status">{actionNames[item.command.action]}: {commandNames[item.command.state]}</small>}
            {canControl && item.can_control && item.session_id && item.state !== "finished" && <div className="button-row">
              {(["open", "retry", "finish", "reveal"] as const).map(action => <button type="button" key={action}
                disabled={!!busy || denied || !connected || item.command?.state === "pending"} onClick={() => void command(item, action)}>{actionNames[action]}</button>)}
            </div>}
          </li>;
        })}
      </ul>
      {error && <p role="alert" className="error">{error}</p>}
    </DocumentDialog>}
  </>;
}
