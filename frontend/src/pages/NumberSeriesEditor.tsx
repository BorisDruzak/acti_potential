import { useState } from "react";
import { Feedback } from "../components/Feedback";
import type { NumberSeries } from "../engine/numbering";
import { numberPermissions } from "../engine/numbering";
import type { DocumentType, Registry } from "../engine/schema";
import { useAction, useData } from "../engine/useData";

export function NumberSeriesEditor({ series, onSaved, onClose }: {
  series?: NumberSeries; onSaved: (id: string) => void; onClose: () => void;
}) {
  const registries = useData<Registry[]>("/registries");
  const types = useData<DocumentType[]>("/document-types");
  const [draft, setDraft] = useState(() => ({
    code: series?.code || "", name: series?.name || "", description: series?.description || "",
    registry_id: series?.registry_id || "", document_type_ids: series?.document_type_ids || [],
    organization_scope: series?.organization_scope || "", period_mode: series?.period_mode || "annual",
    timezone: series?.timezone || "Asia/Yekaterinburg", boundary_month: series?.boundary_month || 1,
    boundary_day: series?.boundary_day || 1, current_period: series?.current_period || null,
    start: series?.start || 1, step: series?.step || 1, prefix: series?.prefix || "",
    suffix: series?.suffix || "", padding: series?.padding ?? 4,
    format: series?.format || "{prefix}{period}/{ordinal}{suffix}", state: series?.state || "active",
    max_batch: series?.max_batch || 20, reservation_hours: series ? series.reservation_hours : 24,
    max_reservation_hours: series?.max_reservation_hours || 8760,
    allow_indefinite: series?.allow_indefinite || false, synthetic: true,
  }));
  const action = useAction();
  const set = (patch: Partial<typeof draft>) => setDraft(current => ({...current, ...patch}));
  const registry = registries.data?.find(item => item.id === draft.registry_id);
  return <form className="panel compact-form" onSubmit={event => {
    event.preventDefault();
    void action.run(async () => {
      const result = await action.request<NumberSeries>(`/number-series${series ? "/" + series.id : ""}`, series ? "PATCH" : "POST", draft);
      onSaved(result.id); onClose();
    }, "Серия сохранена");
  }}>
    <h2>{series ? "Настройки серии" : "Новая серия номеров"}</h2>
    <p>Пилотные серии используют отдельную тестовую нумерацию. Исторические номера ACTI v2 сохраняются.</p>
    <div className="form-grid">
      <label>Название серии<input required value={draft.name} onChange={event => set({name: event.target.value})} /></label>
      <label>Код серии<input required pattern="[a-z][a-z0-9_]*" value={draft.code} disabled={!!series} onChange={event => set({code: event.target.value})} /></label>
      <label>Реестр серии<select required value={draft.registry_id} disabled={!!series} onChange={event => set({registry_id: event.target.value, document_type_ids: []})}><option value="">Выберите реестр</option>{registries.data?.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label>Организационная область<input value={draft.organization_scope} onChange={event => set({organization_scope: event.target.value})} /></label>
      <label>Описание серии<textarea value={draft.description} onChange={event => set({description: event.target.value})} /></label>
      <label>Состояние серии<select value={draft.state} onChange={event => set({state: event.target.value as typeof draft.state})}><option value="active">Активна</option><option value="blocked">Заблокирована</option></select></label>
    </div>
    <fieldset><legend>Допустимые типы документов</legend><p className="muted">Без выбора — все типы выбранного реестра.</p><div className="check-list">{types.data?.filter(type => registry?.document_type_ids.includes(type.id)).map(type => <label key={type.id}><input type="checkbox" checked={draft.document_type_ids.includes(type.id)} onChange={event => set({document_type_ids: event.target.checked ? [...draft.document_type_ids, type.id] : draft.document_type_ids.filter(id => id !== type.id)})} />{type.name}</label>)}</div></fieldset>
    <fieldset><legend>Период и формат</legend><div className="form-grid">
      <label>Правило периода<select value={draft.period_mode} onChange={event => set({period_mode: event.target.value as typeof draft.period_mode})}><option value="annual">Ежегодный</option><option value="manual">Управляемый период</option></select></label>
      <label>Текущий период<input value={draft.current_period || ""} required={draft.period_mode === "manual"} onChange={event => set({current_period: event.target.value || null})} /></label>
      <label>Часовой пояс<input required value={draft.timezone} onChange={event => set({timezone: event.target.value})} /></label>
      <label>Месяц начала периода<input type="number" min="1" max="12" value={draft.boundary_month} onChange={event => set({boundary_month: Number(event.target.value)})} /></label>
      <label>День начала периода<input type="number" min="1" max="31" value={draft.boundary_day} onChange={event => set({boundary_day: Number(event.target.value)})} /></label>
      <label>Начальное значение<input type="number" min="1" step="1" value={draft.start} onChange={event => set({start: Number(event.target.value)})} /></label>
      <label>Шаг нумерации<input type="number" min="1" step="1" value={draft.step} onChange={event => set({step: Number(event.target.value)})} /></label>
      <label>Префикс<input value={draft.prefix} onChange={event => set({prefix: event.target.value})} /></label>
      <label>Суффикс<input value={draft.suffix} onChange={event => set({suffix: event.target.value})} /></label>
      <label>Число разрядов<input type="number" min="1" max="18" value={draft.padding} onChange={event => set({padding: Number(event.target.value)})} /></label>
      <label>Формат номера<input aria-label="Формат номера" required value={draft.format} onChange={event => set({format: event.target.value})} /><small>{"Доступны: {prefix}, {period}, {ordinal}, {suffix}, {code}."}</small></label>
    </div></fieldset>
    <fieldset><legend>Резервирование и сроки</legend><div className="form-grid">
      <label>Максимум номеров за запрос<input type="number" min="1" max="100" value={draft.max_batch} onChange={event => set({max_batch: Number(event.target.value)})} /></label>
      <label>Срок по умолчанию, часов<input type="number" min="1" value={draft.reservation_hours ?? ""} onChange={event => set({reservation_hours: event.target.value ? Number(event.target.value) : null})} /></label>
      <label>Максимальный срок, часов<input type="number" min="1" value={draft.max_reservation_hours} onChange={event => set({max_reservation_hours: Number(event.target.value)})} /></label>
      <label className="checkbox-label"><input type="checkbox" checked={draft.allow_indefinite} onChange={event => set({allow_indefinite: event.target.checked})} />Разрешить бессрочный резерв</label>
    </div></fieldset>
    <Feedback error={registries.error || types.error || action.error} message={action.message} />
    <div className="button-row"><button className="primary" disabled={action.busy}>Сохранить серию</button><button type="button" onClick={onClose}>Отмена</button></div>
  </form>;
}

export function NumberSeriesGrants({series, onSaved}: {series: NumberSeries; onSaved?: () => void}) {
  const users = useData<{id: string; display_name: string; login: string; active: boolean}[]>("/admin/users");
  const grants = useData<{entries: {user_id: string; permissions: string[]}[]}>(`/number-series/${series.id}/grants`);
  const [changes, setChanges] = useState<Record<string, string[]>>({});
  const [selected, setSelected] = useState("");
  const action = useAction();
  const ids = Array.from(new Set([...(grants.data?.entries.map(item => item.user_id) || []), ...Object.keys(changes)]));
  function permissions(id: string) {return changes[id] ?? grants.data?.entries.find(item => item.user_id === id)?.permissions ?? [];}
  return <details className="panel"><summary>Права на серию</summary>
    <p>Права серии выдаются отдельно от доступа к документам.</p>
    <div className="inline-form"><label>Пользователь серии<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">Выберите пользователя</option>{users.data?.filter(user => user.active).map(user => <option key={user.id} value={user.id}>{user.display_name} · {user.login}</option>)}</select></label><button disabled={!selected} onClick={() => {setChanges(current => ({...current, [selected]: permissions(selected)})); setSelected("");}}>Добавить пользователя серии</button></div>
    <form onSubmit={event => {event.preventDefault(); void action.run(async () => {
      await action.request(`/number-series/${series.id}/grants`, "PUT", {entries: ids.map(id => ({user_id: id, permissions: permissions(id)}))});
      setChanges({}); grants.reload(); onSaved?.();
    }, "Права серии сохранены");}}>
      {ids.map(id => <fieldset key={id}><legend>{users.data?.find(user => user.id === id)?.display_name || id}</legend><div className="check-list">{numberPermissions.map(([key, label]) => <label key={key}><input type="checkbox" checked={permissions(id).includes(key)} onChange={event => setChanges(current => ({...current, [id]: event.target.checked ? [...permissions(id), key] : permissions(id).filter(item => item !== key)}))} />{label}</label>)}</div></fieldset>)}
      <Feedback error={users.error || grants.error || action.error} message={action.message} />
      <button disabled={action.busy || grants.loading}>Сохранить права серии</button>
    </form>
  </details>;
}
