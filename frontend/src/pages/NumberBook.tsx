import { useRef, useState } from "react";
import { saveExport } from "../api/client";
import { Feedback } from "../components/Feedback";
import { NumberSeriesEditor, NumberSeriesGrants } from "./NumberSeriesEditor";
import { numberStates, type NumberEntry, type NumberSeries } from "../engine/numbering";
import type { Card } from "../engine/schema";
import { useAction, useData } from "../engine/useData";

function Reservation({series, onSaved}: {series: NumberSeries; onSaved: () => void}) {
  const [count, setCount] = useState(1);
  const [hours, setHours] = useState(series.reservation_hours === null ? "" : String(series.reservation_hours));
  const [reserved, setReserved] = useState<NumberEntry[]>([]);
  const request = useRef<{count: number; reservation_hours: number | null; idempotency_key: string} | null>(null);
  const action = useAction();
  async function reserve(retry = false) {
    if (!retry || !request.current) request.current = {count, reservation_hours: hours ? Number(hours) : null, idempotency_key: crypto.randomUUID()};
    const result = await action.request<{items: NumberEntry[]}>(`/number-series/${series.id}/reserve`, "POST", request.current);
    setReserved(result.items); onSaved();
  }
  return <section className="panel"><h2>Резервирование номеров</h2>
    <form className="inline-form" onSubmit={event => {event.preventDefault(); void action.run(() => reserve(), "Номера зарезервированы");}}>
      <label>Количество номеров<input type="number" min="1" max={series.max_batch} step="1" required value={count} onChange={event => setCount(Number(event.target.value))} /></label>
      <label>Срок резерва, часов<input type="number" min="1" max={series.max_reservation_hours} required={!series.allow_indefinite} value={hours} onChange={event => setHours(event.target.value)} /></label>
      <button className="primary" disabled={action.busy || series.state !== "active"}>Зарезервировать</button>
      {request.current && <button type="button" disabled={action.busy} onClick={() => void action.run(() => reserve(true), "Результат запроса восстановлен")}>Повторить запрос резерва</button>}
    </form>
    <small>До {series.max_batch} номеров за запрос. {series.allow_indefinite ? "Пустой срок — бессрочно." : "Каждый резерв имеет срок действия."} Номера отменённых резервов повторно не выдаются.</small>
    <Feedback {...action} />
    {!!reserved.length && <div role="status">Результат резервирования: {reserved.map(item => item.number).join(", ")}</div>}
  </section>;
}

function EntryHistory({entry}: {entry: NumberEntry}) {
  const [open, setOpen] = useState(false);
  const history = useData<{items: {id: string; action: string; created_at: string; reason?: string}[]}>(open ? `/number-entries/${entry.id}/history` : null);
  return <details onToggle={event => setOpen(event.currentTarget.open)}><summary>История номера {entry.number}</summary><Feedback {...history}/>{history.data?.items.map(item => <p key={item.id}>{new Date(item.created_at).toLocaleString("ru-RU")} · {item.action === "linked" ? "Связан с черновиком" : numberStates[item.action] || item.action}{item.reason ? " · " + item.reason : ""}</p>)}</details>;
}

function NumberIntervals({series}: {series: NumberSeries}) {
  const [from, setFrom] = useState(series.start);
  const [to, setTo] = useState(series.start + series.step * 99);
  const [period, setPeriod] = useState("");
  const [query, setQuery] = useState<string | null>(null);
  const intervals = useData<{period_key: string; step: number; intervals: {from_ordinal: number; to_ordinal: number; state: string}[]}>(query);
  return <details className="panel"><summary>Занятые и свободные интервалы</summary>
    <form className="inline-form" onSubmit={event => {event.preventDefault(); const next = `/number-series/${series.id}/intervals?${new URLSearchParams({from_ordinal: String(from), to_ordinal: String(to), ...(period ? {period_key: period} : {})})}`; if (next === query) intervals.reload(); else setQuery(next);}}>
      <label>Период интервалов<input value={period} onChange={event => setPeriod(event.target.value)} /></label>
      <label>Начиная с<input type="number" min={series.start} step={series.step} required value={from} onChange={event => setFrom(Number(event.target.value))} /></label>
      <label>До значения<input type="number" min={from} step={series.step} required value={to} onChange={event => setTo(Number(event.target.value))} /></label>
      <button disabled={intervals.loading}>Найти интервалы</button>
    </form>
    <Feedback {...intervals}/>
    {intervals.data && <><p>Период: {intervals.data.period_key} · шаг {intervals.data.step}</p><div className="table-wrap"><table><thead><tr><th>С</th><th>По</th><th>Состояние</th></tr></thead><tbody>{intervals.data.intervals.map((item, index) => <tr key={index}><td>{item.from_ordinal}</td><td>{item.to_ordinal}</td><td>{item.state === "free" ? "Свободно" : item.state === "unavailable" ? "Недоступно" : numberStates[item.state] || item.state}</td></tr>)}</tbody></table></div></>}
  </details>;
}

function EntryActions({entry, series, onSaved}: {entry: NumberEntry; series: NumberSeries; onSaved: () => void}) {
  const [operation, setOperation] = useState("");
  const [reason, setReason] = useState("");
  const [query, setQuery] = useState("");
  const [documentId, setDocumentId] = useState("");
  const documents = useData<{items: Card[]}>(operation === "assign" ? `/documents?registry_id=${series.registry_id}&page_size=100&q=${encodeURIComponent(query)}` : null);
  const selected = documents.data?.items.find(item => item.id === documentId);
  const pending = useRef<{body: unknown; operation: string; signature: string} | null>(null);
  const action = useAction();
  async function apply() {
    const data = operation === "assign" ? {document_id: documentId, revision: selected?.revision} : {reason};
    const signature = JSON.stringify([operation, data]);
    if (pending.current?.signature !== signature) pending.current = {operation, signature, body: {...data, idempotency_key: crypto.randomUUID()}};
    await action.request(`/number-entries/${entry.id}/${operation}`, "POST", pending.current!.body);
    setOperation(""); onSaved();
  }
  return <>
    <div className="button-row">
      {entry.state === "reserved" && series.permissions.includes("assign") && <button onClick={() => setOperation("assign")}>Присвоить номер</button>}
      {entry.state === "reserved" && series.permissions.includes("cancel") && <button onClick={() => setOperation("cancel")}>Отменить резерв</button>}
      {entry.state === "reserved" && entry.expires_at && new Date(entry.expires_at).getTime() <= Date.now() && series.permissions.includes("cancel") && <button onClick={() => setOperation("expire")}>Зафиксировать истечение</button>}
      {entry.state === "assigned" && series.permissions.includes("void") && <button onClick={() => setOperation("void")}>Погасить номер</button>}
    </div>
    {operation && <form className="compact-form" onSubmit={event => {event.preventDefault(); void action.run(apply, "Операция выполнена");}}>
      <h3>{operation === "assign" ? "Присвоение документа" : operation === "cancel" ? "Отмена резерва" : operation === "expire" ? "Истечение срока резерва" : "Погашение номера"}: {entry.number}</h3>
      {operation === "assign" ? <>
        <label>Поиск документа для номера<input type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <label>Документ для номера<select required value={documentId} onChange={event => setDocumentId(event.target.value)}><option value="">Выберите документ</option>{documents.data?.items.filter(item => !series.document_type_ids.length || series.document_type_ids.includes(item.document_type_id)).map(item => <option key={item.id} value={item.id}>{item.title || "Документ"}{item.reg_number ? " · " + item.reg_number : ""}</option>)}</select></label>
        <Feedback {...documents}/>
      </> : <label>Причина операции<textarea required minLength={3} value={reason} onChange={event => setReason(event.target.value)} /></label>}
      <Feedback {...action}/><div className="button-row"><button className="primary" disabled={action.busy || (operation === "assign" && !selected)}>Подтвердить операцию</button><button type="button" onClick={() => setOperation("")}>Закрыть операцию</button></div>
    </form>}
  </>;
}

function SeriesJournal({series, readOnly, admin, onPermissionsSaved}: {series: NumberSeries; readOnly: boolean; admin: boolean; onPermissionsSaved: () => void}) {
  const [period, setPeriod] = useState("");
  const [state, setState] = useState("");
  const [search, setSearch] = useState("");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);
  const [applied, setApplied] = useState("");
  const params = `page=${page}&page_size=20${applied}`;
  const entries = useData<{items: NumberEntry[]; total: number}>(`/number-series/${series.id}/entries?${params}`);
  const stats = useData<{counts: Record<string, number>; period_key: string; next_ordinal?: number; next_number?: string}>(`/number-series/${series.id}/stats${period ? "?period_key=" + encodeURIComponent(period) : ""}`);
  const action = useAction();
  const reload = () => {entries.reload(); stats.reload();};
  return <>
    <section className="panel"><h2>Состояние серии</h2><Feedback {...stats}/>
      {stats.data && <><p>Период: {stats.data.period_key}{stats.data.next_number ? " · Следующий номер: " + stats.data.next_number : ""}</p><div className="check-list">{Object.entries(stats.data.counts).map(([key, count]) => <span className="badge" key={key}>{numberStates[key] || key}: {count}</span>)}</div></>}
    </section>
    {!readOnly && series.permissions.includes("reserve") && <Reservation series={series} onSaved={reload}/>}
    {!readOnly && series.permissions.includes("reserve") && <NumberIntervals series={series}/>}
    <form className="panel filters" onSubmit={event => {
      event.preventDefault(); const next = new URLSearchParams();
      if (period) next.set("period_key", period); if (state) next.set("state", state); if (search) next.set("search", search); if (mine && !readOnly) next.set("mine", "true");
      setApplied("&" + next.toString()); setPage(1);
    }}>
      <label>Период журнала<input value={period} onChange={event => setPeriod(event.target.value)} /></label>
      <label>Статус номера<select value={state} onChange={event => setState(event.target.value)}><option value="">Все статусы</option>{Object.entries(numberStates).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Поиск номера<input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label>
      {!readOnly && <label className="checkbox-label"><input type="checkbox" checked={mine} onChange={event => setMine(event.target.checked)} />Только мои резервы</label>}
      <button className="primary">Найти номера</button>
    </form>
    <div className="toolbar"><h2>Журнал регистрации</h2><div className="button-row">{(["csv", "xlsx"] as const).map(format => <button key={format} disabled={action.busy} onClick={() => void action.run(() => saveExport(`/number-series/${series.id}/export?format=${format}${applied}`, `number-register.${format}`), "Журнал выгружен")}>Экспорт номеров {format.toUpperCase()}</button>)}<button onClick={reload}>Обновить журнал</button></div></div>
    <Feedback error={entries.error || action.error} loading={entries.loading} message={action.message}/>
    <p className="muted">Найдено номеров: {entries.data?.total ?? "…"}</p>
    <div className="table-wrap"><table><thead><tr><th>Номер</th><th>Период</th><th>Статус</th><th>Срок резерва</th><th>Документ</th><th>Действия</th></tr></thead><tbody>{entries.data?.items.map(entry => <tr key={entry.id}>
      <td><strong>{entry.number}</strong></td><td>{entry.period_key}</td><td>{numberStates[entry.state] || entry.state}</td><td>{entry.expires_at ? new Date(entry.expires_at).toLocaleString("ru-RU") : "Бессрочно"}</td><td>{entry.document_id ? <a href={`/${readOnly ? "view" : "work"}?document=${encodeURIComponent(entry.document_id)}`}>{entry.document_title || "Документ"}</a> : "—"}</td>
      <td>{!readOnly && <EntryActions entry={entry} series={series} onSaved={reload}/>}<EntryHistory entry={entry}/></td>
    </tr>)}</tbody></table></div>
    {entries.data?.items.length === 0 && <p className="empty-state">Нет номеров по выбранным условиям.</p>}
    <div className="pagination"><button disabled={page === 1 || entries.loading} onClick={() => setPage(current => current - 1)}>Назад</button><span>Страница {page}</span><button disabled={!entries.data || page * 20 >= entries.data.total || entries.loading} onClick={() => setPage(current => current + 1)}>Вперёд</button></div>
    {admin && <NumberSeriesGrants series={series} onSaved={onPermissionsSaved}/>}
  </>;
}

export function NumberBook({admin = false, readOnly = false}: {admin?: boolean; readOnly?: boolean}) {
  const series = useData<NumberSeries[]>("/number-series");
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState<"create" | "edit" | null>(null);
  const current = series.data?.find(item => item.id === selected);
  return <>
    <div className="toolbar"><div><h1>{admin ? "Серии номеров" : "Реестр номеров"}</h1><p>Резервирование, присвоение документам и история регистрации.</p></div>{admin && <button className="primary" onClick={() => setEditing("create")}>Создать серию номеров</button>}</div>
    <Feedback {...series}/>
    {editing && <NumberSeriesEditor key={editing === "create" ? "new" : current?.id} series={editing === "edit" ? current : undefined} onClose={() => setEditing(null)} onSaved={id => {setSelected(id); series.reload();}}/>}
    <div className="toolbar"><label>Серия номеров<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">Выберите серию</option>{series.data?.map(item => <option key={item.id} value={item.id}>{item.name} · {item.code}{item.state === "blocked" ? " · заблокирована" : ""}</option>)}</select></label>{admin && current && <button onClick={() => setEditing("edit")}>Настроить серию</button>}</div>
    {current ? current.permissions.includes("view") ? <SeriesJournal key={current.id} series={current} readOnly={readOnly} admin={admin} onPermissionsSaved={series.reload}/> : <><p className="empty-state">Для просмотра журнала предоставьте доступ к реестру и право просмотра серии.</p>{admin && <NumberSeriesGrants key={current.id} series={current} onSaved={series.reload}/>}</> : <p className="empty-state">Выберите доступную серию номеров.</p>}
  </>;
}

export function CardNumberAssignment({card, onSaved}: {card: Card; onSaved: () => void}) {
  const series = useData<NumberSeries[]>("/number-series");
  const bindings = card.schema.sections.flatMap(section => section.fields).filter(field => field.number_series_id && !card.values[field.key]);
  return <>{bindings.map(field => {
    const current = series.data?.find(item => item.id === field.number_series_id);
    return current && card.permissions.includes("update") && current.permissions.includes("assign") ? <CardSeriesAssignment key={field.key} card={card} series={current} label={field.label} onSaved={onSaved}/> : null;
  })}<Feedback error={series.error}/></>;
}

function CardSeriesAssignment({card, series, label, onSaved}: {card: Card; series: NumberSeries; label: string; onSaved: () => void}) {
  const entries = useData<{items: NumberEntry[]}>(`/number-series/${series.id}/entries?state=reserved&mine=true&page_size=100`);
  const [selected, setSelected] = useState("");
  const pending = useRef<{id: string; key: string} | null>(null);
  const action = useAction();
  return <section className="panel"><h3>Присвоить регистрационный номер: {label}</h3>
    <p>Серия: {series.name}. Номер выбирается из ваших резервов в реестре номеров.</p>
    <form className="inline-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {
      if (pending.current?.id !== selected) pending.current = {id: selected, key: crypto.randomUUID()};
      await action.request(`/number-entries/${selected}/assign`, "POST", {document_id: card.id, revision: card.revision, idempotency_key: pending.current!.key});
      entries.reload(); onSaved();
    }, "Номер присвоен документу");}}>
      <label>Резерв для карточки<select required value={selected} onChange={event => setSelected(event.target.value)}><option value="">Выберите зарезервированный номер</option>{entries.data?.items.map(entry => <option key={entry.id} value={entry.id}>{entry.number}</option>)}</select></label>
      <button className="primary" disabled={action.busy || !selected}>Присвоить номер карточке</button>
    </form><Feedback error={entries.error || action.error} message={action.message}/>
  </section>;
}
