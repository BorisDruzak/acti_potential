import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { OneTimeSecret } from "../components/OneTimeSecret";
import { serviceOperationNames, serviceTokenState, type AdminPage, type OneTimeToken, type ServiceAccount, type ServiceOperation, type ServiceScopeOptions, type ServiceToken } from "../engine/serviceAccounts";
import { useAction, useData } from "../engine/useData";

function ServiceAccountEditor({account, options, onSaved, onClose}: {account?: ServiceAccount; options: ServiceScopeOptions; onSaved: (id: string) => void; onClose: () => void}) {
  const [name, setName] = useState(account?.name || "");
  const [registries, setRegistries] = useState(account?.registry_ids || []);
  const [operations, setOperations] = useState<ServiceOperation[]>(account?.operations || []);
  const [state, setState] = useState(account?.state || "active");
  const [reason, setReason] = useState("");
  const action = useAction();
  const allowedIds = new Set(options.registries.map(registry => registry.id));
  const stale = registries.some(id => !allowedIds.has(id));
  return <form className="panel compact-form service-account-editor" onSubmit={event => {event.preventDefault(); void action.run(async () => {const saved = await action.request<ServiceAccount>(`/admin/service-accounts${account ? "/" + account.id : ""}`, account ? "PATCH" : "POST", {name, registry_ids: registries, operations, ...(account ? {state, reason} : {})}); onSaved(saved.id); onClose();}, "Сервисная учётная запись сохранена");}}>
    <h2>{account ? "Настройка сервисной учётной записи" : "Новая сервисная учётная запись"}</h2><label>Название интеграции<input required maxLength={200} value={name} onChange={event => setName(event.target.value)}/></label>
    <fieldset><legend>Разрешённые реестры</legend>{options.registries.map(registry => <label className="checkbox-label" key={registry.id}><input type="checkbox" checked={registries.includes(registry.id)} onChange={event => setRegistries(current => event.target.checked ? [...current, registry.id] : current.filter(id => id !== registry.id))}/>{registry.name}</label>)}{options.registries.length === 0 && <p>Нет доступных реестров для предоставления сервисного доступа.</p>}{stale && <p>Часть прежней области доступа недоступна. <button type="button" onClick={() => setRegistries(current => current.filter(id => allowedIds.has(id)))}>Убрать недоступные реестры</button></p>}</fieldset>
    <fieldset><legend>Операции чтения API</legend>{options.operations.map(operation => <label className="checkbox-label" key={operation.id}><input type="checkbox" checked={operations.includes(operation.id)} onChange={event => setOperations(current => event.target.checked ? [...current, operation.id] : current.filter(id => id !== operation.id))}/>{serviceOperationNames[operation.id] || operation.label}</label>)}</fieldset>
    <p className="muted">Операции разрешают только чтение. Дополнительно действуют текущие права реестра, документа, поля и файла. Учётная запись не предназначена для входа через браузер.</p>
    {account && <><label>Состояние сервисной записи<select value={state} onChange={event => setState(event.target.value as ServiceAccount["state"])}><option value="active">Активна</option><option value="disabled">Отключена</option></select></label><label>Причина изменения области доступа<textarea required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)}/></label></>}
    <Feedback {...action}/><div className="button-row"><button className="primary" disabled={action.busy || stale || !registries.length || !operations.length || (!!account && !reason.trim())}>Сохранить сервисную запись</button><button type="button" onClick={onClose}>Отмена настройки сервиса</button></div>
  </form>;
}

function TokenAction({account, options, token, revoke = false, onIssued, onSaved, onClose}: {account: ServiceAccount; options: ServiceScopeOptions; token?: ServiceToken; revoke?: boolean; onIssued: (value: OneTimeToken) => void; onSaved: () => void; onClose: () => void}) {
  const [days, setDays] = useState(30);
  const [reason, setReason] = useState("");
  const [registries, setRegistries] = useState((token?.registry_ids || account.registry_ids).filter(id => account.registry_ids.includes(id)));
  const [operations, setOperations] = useState((token?.operations || account.operations).filter(operation => account.operations.includes(operation)));
  const action = useAction();
  const title = revoke ? "Отзыв токена" : token ? "Ротация токена" : "Новый токен интеграции";
  return <form className="panel compact-form token-action" onSubmit={event => {event.preventDefault(); void action.run(async () => {const path = `/admin/service-accounts/${account.id}/tokens${token ? "/" + token.id + (revoke ? "/revoke" : "/rotate") : ""}`; if (revoke) await action.request(path, "POST", {reason}); else {const issued = await action.request<OneTimeToken>(path, "POST", {expires_in_days: days, reason, registry_ids: registries, operations}); onIssued(issued);} onSaved(); onClose();}, revoke ? "Токен отозван" : "Токен выдан");}}>
    <h3>{title}</h3>{token && <p>Идентификатор: {token.prefix} · {serviceTokenState(token)}</p>}{!revoke && <label>Срок токена, дней<input required type="number" min={1} max={90} step={1} value={days} onChange={event => setDays(Number(event.target.value))}/><small>От 1 до 90 дней. После ротации прежний токен перестаёт действовать.</small></label>}
    {!revoke && <><fieldset><legend>Реестры токена</legend>{options.registries.filter(registry => account.registry_ids.includes(registry.id)).map(registry => <label className="checkbox-label" key={registry.id}><input type="checkbox" checked={registries.includes(registry.id)} onChange={event => setRegistries(current => event.target.checked ? [...current, registry.id] : current.filter(id => id !== registry.id))}/>{registry.name}</label>)}</fieldset><fieldset><legend>Операции токена</legend>{account.operations.map(operation => <label className="checkbox-label" key={operation}><input type="checkbox" checked={operations.includes(operation)} onChange={event => setOperations(current => event.target.checked ? [...current, operation] : current.filter(id => id !== operation))}/>{serviceOperationNames[operation]}</label>)}</fieldset><p>Токен получает выбранное подмножество текущей области интеграции. Расширение области интеграции не расширяет уже выпущенные токены.</p></>}
    <label>Причина операции с токеном<textarea required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)}/></label><Feedback {...action}/><div className="button-row"><button className="primary" disabled={action.busy || !reason.trim() || (!revoke && (!registries.length || !operations.length))}>{revoke ? "Подтвердить отзыв токена" : token ? "Выпустить замену токена" : "Выпустить токен"}</button><button type="button" onClick={onClose}>Отмена операции с токеном</button></div>
  </form>;
}

export function ServiceAccountsAdmin() {
  const [page, setPage] = useState(1);
  const [registryId, setRegistryId] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [tokenAction, setTokenAction] = useState<{token?: ServiceToken; revoke?: boolean} | null>(null);
  const [issued, setIssued] = useState<OneTimeToken | null>(null);
  const [tokenPage, setTokenPage] = useState(1);
  const options = useData<ServiceScopeOptions>("/admin/service-accounts/options");
  const list = useData<AdminPage<ServiceAccount>>(`/admin/service-accounts?${new URLSearchParams({page: String(page), page_size: "20", ...(registryId ? {registry_id: registryId} : {})})}`);
  const detail = useData<ServiceAccount>(selectedId ? `/admin/service-accounts/${selectedId}` : null);
  const selected = detail.data;
  const tokens = useData<AdminPage<ServiceToken>>(selectedId ? `/admin/service-accounts/${selectedId}/tokens?page=${tokenPage}&page_size=20` : null);
  function reload() {list.reload(); tokens.reload(); detail.reload();}
  function clearDialogs() {setEditing(false); setCreating(false); setTokenAction(null); setIssued(null);}
  return <section className="service-accounts-admin"><div className="toolbar"><div><h1>API и сервисные учётные записи</h1><p>Ограниченный доступ интеграций к разрешённым данным ACTI.</p></div><button className="primary" disabled={!options.data?.registries.length} onClick={() => {clearDialogs(); setSelectedId(""); setCreating(true);}}>Создать сервисную запись</button></div>
    <label>Фильтр реестра сервисов<select value={registryId} onChange={event => {setRegistryId(event.target.value); setPage(1); setSelectedId(""); clearDialogs();}}><option value="">Все доступные реестры</option>{options.data?.registries.map(registry => <option key={registry.id} value={registry.id}>{registry.name}</option>)}</select></label>
    <Feedback error={options.error || list.error || detail.error} loading={list.loading || options.loading || detail.loading}/>
    {options.data?.registries.length === 0 && <p className="empty-state">Нет доступных реестров для предоставления сервисного доступа.</p>}
    {creating && options.data && <ServiceAccountEditor options={options.data} onSaved={id => {setPage(1); setSelectedId(id); setTokenPage(1); reload();}} onClose={() => setCreating(false)}/>}
    <div className="table-wrap"><table><thead><tr><th>Интеграция</th><th>Статус</th><th>Область чтения</th><th>Действие</th></tr></thead><tbody>{list.data?.items.map(account => <tr key={account.id}><td>{account.name}</td><td>{account.state === "active" ? "Активна" : "Отключена"}</td><td>{account.operations.map(operation => serviceOperationNames[operation]).join(", ")}</td><td><button type="button" onClick={() => {clearDialogs(); setTokenPage(1); setSelectedId(account.id);}}>Открыть сервис</button></td></tr>)}</tbody></table></div>
    {!list.loading && list.data?.items.length === 0 && <p>Сервисных записей по выбранным условиям нет.</p>}
    {selected && <section className="panel service-account-detail"><div className="toolbar"><div><h2>{selected.name}</h2><p>Сервисная запись · только чтение · {selected.state === "active" ? "активна" : "отключена"}</p></div><button type="button" onClick={() => {setTokenAction(null); setEditing(true);}}>Изменить сервисную запись</button></div>
      <p>Реестры: {selected.registry_ids.map(id => options.data?.registries.find(registry => registry.id === id)?.name || "Недоступный реестр").join(", ")}</p>
      {editing && options.data && <ServiceAccountEditor key={selected.id} account={selected} options={options.data} onSaved={reload} onClose={() => setEditing(false)}/>}
      <h3>Токены доступа</h3><p>Хранятся только идентификаторы и сроки. Секрет можно получить лишь при выпуске или ротации.</p>{selected.state === "active" && <button type="button" onClick={() => {setEditing(false); setTokenAction({});}}>Создать токен</button>}
      <Feedback error={tokens.error} loading={tokens.loading}/><div className="table-wrap"><table><thead><tr><th>Идентификатор</th><th>Статус токена</th><th>Область токена</th><th>Создан</th><th>Действует до</th><th>Действия</th></tr></thead><tbody>{tokens.data?.items.map(token => <tr key={token.id}><td>{token.prefix}</td><td>{serviceTokenState(token)}</td><td><p>Реестры: {token.registry_ids?.map(id => options.data?.registries.find(registry => registry.id === id)?.name || "Недоступный реестр").join(", ")}</p><p>{token.operations?.map(operation => serviceOperationNames[operation]).join(", ")}</p></td><td>{token.created_at ? new Date(token.created_at).toLocaleString("ru-RU") : "—"}</td><td>{new Date(token.expires_at).toLocaleString("ru-RU")}</td><td>{!token.revoked_at && <div className="button-row">{selected.state === "active" && serviceTokenState(token) === "Действует" && <button type="button" onClick={() => setTokenAction({token})}>Ротировать токен</button>}<button type="button" onClick={() => setTokenAction({token, revoke: true})}>Отозвать токен</button></div>}</td></tr>)}</tbody></table></div>
      {tokens.data?.items.length === 0 && <p>Токенов пока нет.</p>}{tokens.data && <div className="pagination"><span>Токенов: {tokens.data.total} · страница {tokenPage}</span><button type="button" disabled={tokenPage <= 1} onClick={() => {setTokenAction(null); setTokenPage(value => value - 1);}}>Предыдущие токены</button><button type="button" disabled={tokenPage * tokens.data.page_size >= tokens.data.total} onClick={() => {setTokenAction(null); setTokenPage(value => value + 1);}}>Следующие токены</button></div>}{tokenAction && options.data && <TokenAction key={selected.id + (tokenAction.token?.id || "new") + !!tokenAction.revoke} account={selected} options={options.data} {...tokenAction} onIssued={setIssued} onSaved={() => {setTokenPage(1); reload();}} onClose={() => setTokenAction(null)}/>}
    </section>}
    {list.data && <div className="pagination"><span>Записей: {list.data.total} · страница {page}</span><button type="button" disabled={page <= 1} onClick={() => {clearDialogs(); setSelectedId(""); setPage(value => value - 1);}}>Предыдущие сервисы</button><button type="button" disabled={page * list.data.page_size >= list.data.total} onClick={() => {clearDialogs(); setSelectedId(""); setPage(value => value + 1);}}>Следующие сервисы</button></div>}
    <p>Описание сервисного API: <code>/api/service/v1/openapi.json</code>. Получайте его API-клиентом с выданным токеном.</p>
    {issued && <OneTimeSecret value={issued} onClose={() => setIssued(null)}/>}
  </section>;
}
