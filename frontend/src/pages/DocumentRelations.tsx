import { useState } from "react";
import { saveDownload } from "../api/client";
import { Feedback } from "../components/Feedback";
import { relationLabel, relationStates, relationTypes, type DocumentRelation, type RelationAnchor, type RelationNavigation } from "../engine/relations";
import type { Card } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { RelationEditor } from "./RelationEditor";

function navigation(documentId: string, versionId?: string | null, blockId?: string | null, anchor?: RelationAnchor | null): RelationNavigation {
  return {documentId, versionId: versionId || undefined, ...(anchor?.file_id || blockId ? {contentTarget: {fileId: anchor?.file_id, blockId: blockId || anchor?.block_id, ordinal: anchor?.ordinal ?? anchor?.block_ordinal}} : {})};
}

function RelationBasis({relationId, side, mode, onNavigate}: {relationId: string; side: "source" | "target"; mode: string; onNavigate: (value: RelationNavigation) => void}) {
  const [open, setOpen] = useState(false);
  const basis = useData<{document: Card; anchor: RelationAnchor & {version?: number}; files: {id: string; name: string; version: number; version_id: string}[]; blocks: {id: string; text: string; ordinal: number}[]}>(open ? `/document-relations/${relationId}/basis?side=${side}&mode=${mode}` : null);
  const action = useAction();
  return <details className="relation-basis" onToggle={event => setOpen(event.currentTarget.open)}><summary>{side === "source" ? "Основание исходного документа" : "Основание связанного документа"}</summary>
    <Feedback error={basis.error || action.error} loading={basis.loading} message={action.message}/>
    {basis.data && <><p>{basis.data.document.title || "Документ"} · {basis.data.anchor?.version ? `оригинал версии ${basis.data.anchor.version}` : "карточка"}</p><p className="muted">Реквизиты карточки показаны в текущем разрешённом состоянии. Оригинал и фрагмент закреплены в связи.</p>
      {basis.data.files.map(file => <div className="file-line" key={file.id}><span>{file.name} · версия {file.version}</span><button disabled={action.busy} onClick={() => void action.run(() => saveDownload(file.id, file.name, mode === "viewer" ? "viewer" : "editor"), "Основание скачано")}>Скачать основание версии {file.version}</button></div>)}
      {basis.data.blocks.map(block => <blockquote key={block.id} className="content-text">{block.text}</blockquote>)}
      <button type="button" onClick={() => onNavigate(navigation(basis.data!.document.id, basis.data!.anchor?.version_id, basis.data!.anchor?.block_id, basis.data!.anchor))}>Перейти к документу основания</button>
    </>}
  </details>;
}

function RelationDecision({relation, onSaved}: {relation: DocumentRelation; onSaved: () => void}) {
  const [decision, setDecision] = useState("");
  const [reason, setReason] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const action = useAction();
  return <>
    <div className="button-row">
      {relation.state === "proposed" && relation.permissions.confirm && <button onClick={() => setDecision("confirm")}>Подтвердить связь</button>}
      {relation.state === "proposed" && relation.permissions.reject && <button onClick={() => setDecision("reject")}>Отклонить связь</button>}
    </div>
    {decision && <form className="compact-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {await action.request(`/document-relations/${relation.id}/${decision}`, "POST", {reason, effective_date: date}); setDecision(""); onSaved();}, decision === "confirm" ? "Связь подтверждена" : "Связь отклонена");}}>
      <h4>{decision === "confirm" ? "Подтверждение связи" : "Отклонение связи"}</h4>
      <label>Основание решения<textarea required value={reason} onChange={event => setReason(event.target.value)}/></label>
      <label>Дата действия решения<input type="date" required value={date} onChange={event => setDate(event.target.value)}/></label>
      <Feedback {...action}/><div className="button-row"><button className="primary" disabled={action.busy}>Сохранить решение о связи</button><button type="button" onClick={() => setDecision("")}>Отмена решения</button></div>
    </form>}
  </>;
}

function RelationRow({relation, currentId, readOnly, onSaved, onNavigate}: {
  relation: DocumentRelation; currentId: string; readOnly: boolean; onSaved: () => void; onNavigate: (value: RelationNavigation) => void;
}) {
  const incoming = relation.target_document?.id === currentId;
  const source = relation.source_document;
  const target = relation.target_document;
  return <li id={`relation-${relation.id}`} className={`panel relation-row relation-${relation.state}`}>
    <div className="toolbar"><h3>{relationLabel(relation.relation_type, incoming)}</h3><span className={`badge ${relation.state === "confirmed" ? "green" : ""}`}>{relationStates[relation.state] || relation.state}</span></div>
    <div className="relation-endpoints">
      <div><small>Исходный документ{relation.source_version ? ` · версия ${relation.source_version}` : ""}</small><button type="button" className="text-button" onClick={() => onNavigate(navigation(source.id, relation.source_version_id, relation.source_block_id, relation.source_anchor))}>{source.title || "Документ"}{source.reg_number ? " · " + source.reg_number : ""}{!relation.source_anchor?.file_id && !relation.source_block_id ? " · текущая карточка" : ""}</button>{relation.source_snippet && <blockquote>{relation.source_snippet}</blockquote>}</div>
      <div><small>{target ? "Связанный документ" : "Внешний источник"}{relation.target_version ? ` · версия ${relation.target_version}` : ""}</small>
        {target ? <button type="button" className="text-button" onClick={() => onNavigate(navigation(target.id, relation.target_version_id, relation.target_block_id, relation.target_anchor))}>{target.title || "Документ"}{target.reg_number ? " · " + target.reg_number : ""}{!relation.target_anchor?.file_id && !relation.target_block_id ? " · текущая карточка" : ""}</button> : relation.external_target ? <><a href={relation.external_target.url} target="_blank" rel="noopener noreferrer">{relation.external_target.title}</a><p>{relation.external_target.identifier} · версия {relation.external_target.version} · действует с {relation.external_target.effective_date}</p><small>Источник проверен автором; состояние подтверждения связи указано отдельно.</small></> : null}
        {relation.target_snippet && <blockquote>{relation.target_snippet}</blockquote>}
      </div>
    </div>
    <p>{relation.description}</p>
    <small>Источник связи: {{manual: "Вручную", deterministic: "Правило", imported: "Импорт", ai_suggested: "Предложение AI"}[relation.source] || "Предложение"} · {new Date(relation.created_at).toLocaleString("ru-RU")}{relation.created_by_name || relation.created_by ? " · автор: " + (relation.created_by_name || relation.created_by) : ""}{relation.effective_date ? " · дата действия: " + relation.effective_date : ""}</small>
    {relation.decided_at && <p className="muted">Решение: {new Date(relation.decided_at).toLocaleString("ru-RU")}{relation.decided_by_name || relation.decided_by ? " · " + (relation.decided_by_name || relation.decided_by) : ""}</p>}
    {relation.reason && <p>Основание решения: {relation.reason}</p>}
    {relation.state === "proposed" && <p className="muted">Предложение не меняет официальный статус документа.</p>}
    <div className="relation-bases"><RelationBasis relationId={relation.id} side="source" mode={readOnly ? "viewer" : "editor"} onNavigate={onNavigate}/>{target && <RelationBasis relationId={relation.id} side="target" mode={readOnly ? "viewer" : "editor"} onNavigate={onNavigate}/>}</div>
    {!readOnly && <RelationDecision relation={relation} onSaved={onSaved}/>}
  </li>;
}

type GraphData = {nodes: {id: string; title?: string; reg_number?: string}[]; edges: {id: string; source_document_id: string; target_document_id: string | null; relation_type: string; state: string; label: string; inverse_label: string; source_version_id?: string; target_version_id?: string; source_anchor?: RelationAnchor; target_anchor?: RelationAnchor; source_block_id?: string; target_block_id?: string}[]; truncated: boolean};

function RelationGraph({graph, onNavigate}: {graph: GraphData; onNavigate: (value: RelationNavigation) => void}) {
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  const edges = graph.edges.filter(edge => nodes.has(edge.source_document_id) && edge.target_document_id && nodes.has(edge.target_document_id));
  return <section className="relation-graph" aria-label="Интерактивная схема связей">
    <h3>Схема связей</h3><p className="muted">Стрелка направлена от исходного документа к связанному. Выберите документ для перехода.</p>
    {edges.map(edge => <div className={`relation-graph-row relation-${edge.state}`} key={edge.id}>
      <button className="graph-node" onClick={() => onNavigate(navigation(edge.source_document_id, edge.source_version_id, edge.source_block_id, edge.source_anchor))}>{nodes.get(edge.source_document_id)?.title || "Документ"}<small>{edge.source_anchor?.file_id || edge.source_block_id ? "Закреплённый оригинал" : "Текущая карточка документа"}</small></button>
      <div className="graph-edge"><span>{edge.label || relationLabel(edge.relation_type)} · {relationStates[edge.state]}</span><svg viewBox="0 0 120 20" role="img" aria-label="Направление связи"><path d="M0 10 H112 M104 3 L113 10 L104 17" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray={edge.state === "proposed" ? "5 4" : undefined}/></svg></div>
      <button className="graph-node" onClick={() => onNavigate(navigation(edge.target_document_id!, edge.target_version_id, edge.target_block_id, edge.target_anchor))}>{nodes.get(edge.target_document_id!)?.title || "Документ"}<small>{edge.target_anchor?.file_id || edge.target_block_id ? "Закреплённый оригинал" : "Текущая карточка документа"}</small></button>
    </div>)}
    {!edges.length && <p>Внутренних связей по выбранным условиям нет.</p>}
    {graph.truncated && <p className="muted">Показана часть схемы. Уточните фильтр; полный доступный список приведён ниже.</p>}
  </section>;
}

export function DocumentRelations({card, readOnly, basis = false, onNavigate}: {card: Card; readOnly: boolean; basis?: boolean; onNavigate: (value: RelationNavigation) => void}) {
  const mode = readOnly ? "viewer" : "editor";
  const [type, setType] = useState("");
  const [state, setState] = useState(basis ? "confirmed" : "");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const params = new URLSearchParams({mode, ...(type ? {type} : {}), ...(state ? {state} : {})});
  const relations = useData<{items: DocumentRelation[]; total: number; page: number; page_size: number; status_effects_truncated?: boolean; status_effects: {relation_id: string; relation_type: string; label: string; effective_date: string}[]}>(`/documents/${card.id}/relations?${params}&page=${page}&page_size=20`);
  const graph = useData<GraphData>(`/documents/${card.id}/relation-graph?${params}&limit=100`);
  const reload = () => {relations.reload(); graph.reload();};
  return <section>
    <div className="toolbar"><div><h2>{basis ? "Основания и изменения" : "Связанные акты"}</h2><p>Показаны только связи между доступными вам документами и проверяемые внешние источники.</p></div><div className="button-row"><button onClick={reload}>Обновить связи</button>{!readOnly && card.permissions.includes("update") && <button className="primary" onClick={() => setCreating(true)}>Предложить связь документа</button>}</div></div>
    {creating && <RelationEditor card={card} mode={mode} onSaved={reload} onClose={() => setCreating(false)}/>}
    {basis && <section className="panel"><h3>Подтверждённые основания статуса</h3><p>Предложения и отклонённые связи не изменяют официальный статус.</p>{relations.data?.status_effects.length ? <ul>{relations.data.status_effects.map(effect => <li key={effect.relation_id}><a href={`#relation-${effect.relation_id}`}>{effect.label}</a> · {effect.effective_date}<RelationBasis relationId={effect.relation_id} side="source" mode={mode} onNavigate={onNavigate}/></li>)}</ul> : <p>Подтверждённых оснований изменения статуса нет.</p>}</section>}
    {basis && relations.data?.status_effects_truncated && <p role="status">Показаны первые 200 оснований статуса. Остальные доступные связи можно просмотреть в списке с пагинацией.</p>}
    <div className="panel filters"><label>Фильтр типа связи<select value={type} onChange={event => {setType(event.target.value); setPage(1);}}><option value="">Все типы связей</option>{relationTypes.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>Состояние подтверждения<select value={state} onChange={event => {setState(event.target.value); setPage(1);}}><option value="">Все состояния</option>{Object.entries(relationStates).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
    <Feedback error={relations.error || graph.error} loading={relations.loading || graph.loading}/>
    {graph.data && <RelationGraph graph={graph.data} onNavigate={onNavigate}/>}
    <h3>Доступный список связей</h3><p className="muted">Связей: {relations.data?.total ?? "…"}</p>
    <ul className="relation-list">{relations.data?.items.map(relation => <RelationRow key={relation.id} relation={relation} currentId={card.id} readOnly={readOnly} onSaved={reload} onNavigate={onNavigate}/>)}</ul>
    {relations.data?.items.length === 0 && <p className="empty-state">Связей по выбранным условиям нет.</p>}
    <div className="pagination"><button disabled={page === 1 || relations.loading} onClick={() => setPage(current => current - 1)}>Назад</button><span>Страница {page}</span><button disabled={!relations.data || page * 20 >= relations.data.total || relations.loading} onClick={() => setPage(current => current + 1)}>Вперёд</button></div>
  </section>;
}

export function RelationStatusHistory({card, readOnly, onNavigate}: {card: Card; readOnly: boolean; onNavigate: (value: RelationNavigation) => void}) {
  const [page, setPage] = useState(1);
  const history = useData<{items: {relation_id: string; action: string; created_at: string; reason: string; effective_date: string; applies_now?: boolean; actor_name?: string; actor_id?: string}[]; total?: number}>(`/documents/${card.id}/relation-history?mode=${readOnly ? "viewer" : "editor"}&page=${page}&page_size=20`);
  return <section className="panel"><div className="toolbar"><h2>История статуса</h2><button onClick={history.reload}>Обновить историю статуса</button></div><p>Решения по доступным связям. Подтверждённое основание хранится отдельно от предложения.</p><Feedback {...history}/>{history.data?.items.map((item, index) => <article key={`${item.relation_id}-${index}`} className="content-block"><h3>{{proposed: "Связь предложена", confirmed: "Связь подтверждена", rejected: "Связь отклонена"}[item.action] || "Изменение связи"}</h3><p>{new Date(item.created_at).toLocaleString("ru-RU")}{item.actor_name || item.actor_id ? " · " + (item.actor_name || item.actor_id) : ""}{item.effective_date ? " · дата действия: " + item.effective_date : ""}</p>{item.action === "confirmed" && item.applies_now === false && <p className="muted">Это решение не создаёт действующего изменения статуса текущей версии.</p>}<p>{item.reason}</p><RelationBasis relationId={item.relation_id} side="source" mode={readOnly ? "viewer" : "editor"} onNavigate={onNavigate}/></article>)}{history.data?.items.length === 0 && <p className="empty-state">История доступных решений пока пуста.</p>}{history.data?.total !== undefined && history.data.total > 20 && <div className="pagination"><button disabled={page === 1 || history.loading} onClick={() => setPage(current => current - 1)}>Назад</button><span>Страница {page}</span><button disabled={page * 20 >= history.data.total || history.loading} onClick={() => setPage(current => current + 1)}>Вперёд</button></div>}</section>;
}
