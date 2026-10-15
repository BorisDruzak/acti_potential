import { useState } from "react";
import { Feedback } from "../components/Feedback";
import type { ContentBlock } from "../engine/content";
import { relationTypes, type RelationSelection, type RelationVersion } from "../engine/relations";
import type { Card } from "../engine/schema";
import { useAction, useData } from "../engine/useData";

export function RelationAnchorPicker({documentId, prefix, mode, value, onChange}: {
  documentId: string; prefix: string; mode: string; value: RelationSelection;
  onChange: (value: RelationSelection) => void;
}) {
  const versions = useData<{versions: RelationVersion[]}>(`/documents/${documentId}/relation-options?mode=${mode}`);
  const [extractionId, setExtractionId] = useState("");
  const [page, setPage] = useState(1);
  const selected = versions.data?.versions.find(version => version.id === value.version_id);
  const blocks = useData<{items: ContentBlock[]; total: number}>(extractionId ? `/content/extractions/${extractionId}/blocks?page=${page}&page_size=200&mode=${mode}` : null);
  return <fieldset><legend>{prefix}: версия и основание</legend>
    <label>Версия: {prefix}<select value={value.version_id} onChange={event => {onChange({version_id: event.target.value, block_id: null}); setExtractionId(""); setPage(1);}}><option value="">Карточка без привязки к версии</option>{versions.data?.versions.map(version => <option key={version.id} value={version.id}>Версия {version.version}</option>)}</select></label>
    {selected && <>
      <label>Оригинал: {prefix}<select value={extractionId} onChange={event => {setExtractionId(event.target.value); setPage(1); onChange({...value, block_id: null});}}><option value="">Связь со всей версией документа</option>{selected.files.flatMap(file => file.extractions.filter(item => item.state === "completed").map(item => <option key={item.id} value={item.id}>{file.name}</option>))}</select></label>
      {extractionId && <>
        <label>Фрагмент: {prefix}<select value={value.block_id || ""} onChange={event => onChange({...value, block_id: event.target.value || null})}><option value="">Весь документ</option>{blocks.data?.items.map(block => <option key={block.id} value={block.id}>{block.page ? `Стр. ${block.page} · ` : ""}{block.ordinal + 1}. {block.text.slice(0, 150)}</option>)}</select></label>
        {!!blocks.data && blocks.data.total > 200 && <div className="button-row"><button type="button" disabled={page === 1 || blocks.loading} onClick={() => setPage(current => current - 1)}>Предыдущие фрагменты: {prefix}</button><span>Страница {page}</span><button type="button" disabled={page * 200 >= blocks.data.total || blocks.loading} onClick={() => setPage(current => current + 1)}>Следующие фрагменты: {prefix}</button></div>}
      </>}
    </>}
    <Feedback error={versions.error || blocks.error} loading={versions.loading || blocks.loading}/>
  </fieldset>;
}

export function RelationEditor({card, mode, onSaved, onClose}: {card: Card; mode: string; onSaved: () => void; onClose: () => void}) {
  const [type, setType] = useState("reference");
  const [query, setQuery] = useState("");
  const candidates = useData<{items: Card[]}>(`/documents?q=${encodeURIComponent(query)}&page_size=100&mode=${mode}`);
  const [targetId, setTargetId] = useState("");
  const [external, setExternal] = useState(false);
  const [description, setDescription] = useState("");
  const [source, setSource] = useState<RelationSelection>({version_id: "", block_id: null});
  const [target, setTarget] = useState<RelationSelection>({version_id: "", block_id: null});
  const [outside, setOutside] = useState({url: "", title: "", identifier: "", version: "", effective_date: "", verified: false});
  const action = useAction();
  return <form className="panel compact-form relation-editor" onSubmit={event => {event.preventDefault(); void action.run(async () => {
    if (external && new URL(outside.url).protocol !== "https:") throw new Error("Укажите HTTPS-ссылку на внешний акт.");
    await action.request("/document-relations", "POST", {
      source_document_id: card.id, source_version_id: source.version_id || null, source_block_id: source.block_id,
      relation_type: type, source: "manual", description,
      ...(external ? {external_target: outside} : {target_document_id: targetId, target_version_id: target.version_id || null, target_block_id: target.block_id}),
    });
    onSaved(); onClose();
  }, "Связь предложена для подтверждения");}}>
    <h3>Новая связь документа</h3><p>Предложенная связь не изменяет официальный статус документа.</p>
    <label>Тип связи<select value={type} onChange={event => setType(event.target.value)}>{relationTypes.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    <RelationAnchorPicker documentId={card.id} prefix="Исходный документ" mode={mode} value={source} onChange={setSource}/>
    <label className="checkbox-label"><input type="checkbox" checked={external} onChange={event => setExternal(event.target.checked)}/>Внешний акт</label>
    {external ? <fieldset><legend>Проверяемый внешний источник</legend>
      <label>Название внешнего акта<input required value={outside.title} onChange={event => setOutside(current => ({...current, title: event.target.value}))}/></label>
      <label>HTTPS-ссылка на акт<input type="url" pattern="https://.*" required value={outside.url} onChange={event => setOutside(current => ({...current, url: event.target.value}))}/></label>
      <label>Реквизиты внешнего акта<input required value={outside.identifier} onChange={event => setOutside(current => ({...current, identifier: event.target.value}))}/></label>
      <label>Версия внешнего акта<input required value={outside.version} onChange={event => setOutside(current => ({...current, version: event.target.value}))}/></label>
      <label>Дата действия внешнего акта<input type="date" required value={outside.effective_date} onChange={event => setOutside(current => ({...current, effective_date: event.target.value}))}/></label>
      <label className="checkbox-label"><input type="checkbox" required checked={outside.verified} onChange={event => setOutside(current => ({...current, verified: event.target.checked}))}/>Я проверил ссылку, реквизиты и версию источника</label>
      <p className="muted">Отметка фиксирует вашу проверку источника. Связь отдельно подтверждает уполномоченный сотрудник.</p>
    </fieldset> : <>
      <label>Поиск связанного документа<input type="search" value={query} onChange={event => setQuery(event.target.value)}/></label>
      <label>Связанный документ<select required value={targetId} onChange={event => {setTargetId(event.target.value); setTarget({version_id: "", block_id: null});}}><option value="">Выберите доступный документ</option>{type === "previous_revision" && <option value={card.id}>Этот документ · другая версия</option>}{candidates.data?.items.filter(item => item.id !== card.id && item.permissions?.includes("update")).map(item => <option key={item.id} value={item.id}>{[item.reg_number, item.title || "Документ"].filter(Boolean).join(" · ")}</option>)}</select></label>
      {targetId && <RelationAnchorPicker key={targetId} documentId={targetId} prefix="Связанный документ" mode={mode} value={target} onChange={setTarget}/>}
    </>}
    <label>Пояснение связи<textarea required value={description} onChange={event => setDescription(event.target.value)}/></label>
    <Feedback error={candidates.error || action.error} message={action.message}/>
    <div className="button-row"><button className="primary" disabled={action.busy || (!external && !targetId) || (type === "previous_revision" && targetId === card.id && (!source.version_id || !target.version_id || source.version_id === target.version_id))}>Предложить связь</button><button type="button" onClick={onClose}>Закрыть новую связь</button></div>
  </form>;
}
