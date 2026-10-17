import { useState } from "react";
import { encodeFile, saveExport } from "../api/client";
import { Feedback } from "../components/Feedback";
import type { ReferenceItem, ReferenceSet } from "../engine/schema";
import { useAction, useData } from "../engine/useData";

const peopleCodes = new Set(["executors", "responsible_executors", "signers", "approvers"]);
const metadataText = (item: ReferenceItem | undefined, key: string) => typeof item?.metadata?.[key] === "string" ? String(item.metadata[key]) : "";

function ItemEditor({ referenceId, participant, item, items, reload, close }: {
  referenceId: string; participant: boolean; item?: ReferenceItem; items: ReferenceItem[]; reload: () => void; close?: () => void;
}) {
  const [position, setPosition] = useState(metadataText(item, "position"));
  const [department, setDepartment] = useState(metadataText(item, "department"));
  const [label, setLabel] = useState(item?.label || "");
  const [code, setCode] = useState(item?.code || "");
  const [description, setDescription] = useState(item?.description || "");
  const [sort, setSort] = useState(item?.sort_order || 0);
  const [active, setActive] = useState(item?.active ?? true);
  const [parent, setParent] = useState(item?.parent_id || "");
  const [from, setFrom] = useState(item?.valid_from || "");
  const [until, setUntil] = useState(item?.valid_until || "");
  const action = useAction();
  return <form className="compact-form reference-row" onSubmit={event => {
    event.preventDefault();
    void action.run(async () => {
      await action.request(`/references/${referenceId}/items${item ? "/" + item.id : ""}`, item ? "PATCH" : "POST", {
        ...(participant ? {metadata: {...(item?.metadata || {}), position, department}} : {}),
        label, code, description, sort_order: sort, active, parent_id: parent || null, valid_from: from || null, valid_until: until || null,
      });
      reload();
      if (!item) { setLabel(""); setCode(""); setDescription(""); setPosition(""); setDepartment(""); }
      close?.();
    }, "Значение сохранено");
  }}>
    <div className="form-grid">
      <label>{item ? "Значение" : "Новое значение"}<input aria-label={item ? "Значение: " + item.label : "Новое значение"} required value={label} onChange={event => setLabel(event.target.value)} /></label>
      {participant && <><label>Должность<input maxLength={255} value={position} onChange={event => setPosition(event.target.value)} /></label><label>Подразделение<input maxLength={255} value={department} onChange={event => setDepartment(event.target.value)} /></label></>}
      <label>Код значения<input value={code} onChange={event => setCode(event.target.value)} /></label>
      <label>Описание значения<textarea value={description} onChange={event => setDescription(event.target.value)} /></label>
      <label>Порядок<input type="number" step="1" value={sort} onChange={event => setSort(Number(event.target.value))} /></label>
      <label>Родительское значение<select value={parent} onChange={event => setParent(event.target.value)}><option value="">Без родителя</option>{items.filter(current => current.id !== item?.id).map(current => <option key={current.id} value={current.id}>{current.label}</option>)}</select></label>
      <label>Действует с<input type="date" value={from} onChange={event => setFrom(event.target.value)} /></label>
      <label>Действует по<input type="date" min={from || undefined} value={until} onChange={event => setUntil(event.target.value)} /></label>
      <label className="checkbox-label"><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} />Активно</label>
    </div>
    <Feedback {...action} />
    <div className="button-row"><button className="primary" disabled={action.busy}>{item ? "Сохранить значение" : "Добавить значение"}</button>{close && <button type="button" onClick={close}>Отмена</button>}</div>
  </form>;
}

export function ReferenceItems({ reference }: { reference: ReferenceSet }) {
  const participant = peopleCodes.has(reference.code);
  const items = useData<ReferenceItem[]>(`/references/${reference.id}/items`);
  const [historyOpen, setHistoryOpen] = useState(false);
  const history = useData<{items: {id: string; action: string; occurred_at: string; details?: Record<string, unknown>}[]}>(historyOpen ? `/references/${reference.id}/history` : null);
  const [name, setName] = useState(reference.name);
  const [description, setDescription] = useState(reference.description || "");
  const [editing, setEditing] = useState("");
  const action = useAction();
  return <section className="panel">
    <h2>{reference.name}</h2>
    <details><summary>Настройки справочника</summary>
      <form className="compact-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {await action.request(`/references/${reference.id}`, "PATCH", {name, description});}, "Настройки сохранены");}}>
        <label>Название справочника<input required value={name} onChange={event => setName(event.target.value)} /></label>
        <label>Описание справочника<textarea value={description} onChange={event => setDescription(event.target.value)} /></label>
        <button disabled={action.busy}>Сохранить настройки</button>
      </form><p className="muted">Код: {reference.code}. Область справочника сохраняется для существующих связей.</p>
    </details>
    <details><summary>Добавить значение</summary><ItemEditor participant={participant} referenceId={reference.id} items={items.data || []} reload={items.reload} /></details>
    <div className="toolbar">
      <div className="button-row">{(["csv", "xlsx"] as const).map(format => <button key={format} disabled={action.busy} onClick={() => void action.run(() => saveExport(`/references/${reference.id}/export?format=${format}`, `reference.${format}`))}>Экспорт {format.toUpperCase()}</button>)}
      <button onClick={() => setHistoryOpen(current => !current)}>Журнал изменений</button></div>
      <label>Импорт CSV / XLSX<input type="file" accept=".csv,.xlsx" disabled={action.busy} onChange={event => {
        const file = event.target.files?.[0];
        if (file) void action.run(async () => {
          if (file.size > 2 * 1024 * 1024) throw new Error("Файл превышает 2 МиБ");
          await action.request(`/references/${reference.id}/import`, "POST", {format: file.name.toLowerCase().endsWith(".xlsx") ? "xlsx" : "csv", content: await encodeFile(file)});
          items.reload(); history.reload();
        }, "Справочник импортирован");
        event.target.value = "";
      }} /></label>
    </div>
    <p className="muted">Колонки импорта: id, code, label, description, sort_order, active, parent_id, valid_from, valid_until{participant ? ", position, department" : ""}. Пустой id создаёт значение; существующий id обновляет его. Исходные идентификаторы сохраняются.</p>
    <Feedback error={items.error || action.error} message={action.message} loading={items.loading} />
    <div className="reference-list">{items.data?.map(item => <div key={item.id} className="reference-row">
      {editing === item.id ? <ItemEditor participant={participant} referenceId={reference.id} item={item} items={items.data || []} reload={() => {items.reload(); history.reload();}} close={() => setEditing("")} /> : <div className="toolbar"><div><strong>{item.label}</strong>{participant && <p>{[metadataText(item, "position"), metadataText(item, "department")].filter(Boolean).join(" · ")}</p>}<small>{item.code || "Без кода"} · порядок {item.sort_order || 0}{item.active ? "" : " · неактивно"}{item.selectable === false && item.active ? " · вне срока действия" : ""}{item.parent_id ? " · родитель: " + (items.data?.find(parent => parent.id === item.parent_id)?.label || "—") : ""}</small><p>{item.description}</p></div><button onClick={() => setEditing(item.id)}>Изменить: {item.label}</button></div>}
    </div>)}</div>
    {items.data?.length === 0 && <p className="empty-state">В справочнике пока нет значений.</p>}
    {historyOpen && <section><h3>Журнал изменений справочника</h3><Feedback {...history}/>{history.data?.items.map(item => <p key={item.id}>{new Date(item.occurred_at).toLocaleString("ru-RU")} · {item.action}</p>)}</section>}
  </section>;
}
