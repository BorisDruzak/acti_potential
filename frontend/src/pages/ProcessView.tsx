import { useState } from "react";
import { saveDownload, saveExport } from "../api/client";
import { Feedback } from "../components/Feedback";
import { ProcessGraph } from "../components/ProcessGraph";
import type { ProcessRecord, ProcessStep, ProcessVersion } from "../engine/processes";
import type { Card } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import type { RelationAnchor } from "../engine/relations";
import { RelatedKnowledge } from "./Knowledge";

type Basis = {restricted: boolean; label?: string; document?: Card; anchor?: RelationAnchor & {version?: number}; files?: {id: string; name: string; version: number}[]; blocks?: {id: string; text: string}[]; note?: string; template?: {name: string; version: number; template_version_id: string}};
function ProcessBasis({versionId, step, mode, templates = false}: {versionId: string; step: ProcessStep; mode: string; templates?: boolean}) {
  const [open, setOpen] = useState(false);
  const data = useData<{items: Basis[]}>(open ? `/process-versions/${versionId}/steps/${encodeURIComponent(step.key)}/${templates ? "templates" : "basis"}?mode=${mode}` : null);
  const action = useAction();
  return <section className="process-basis"><button type="button" onClick={() => setOpen(current => !current)}>{templates ? "Шаблоны и образцы шага" : "Почему этот шаг существует?"}</button>
    {open && <div className="panel"><h4>{templates ? "Закреплённые шаблоны" : "Основания шага"}</h4><Feedback error={data.error || action.error} loading={data.loading} message={action.message}/>
      {data.data?.items.map((item, index) => item.restricted ? <p key={index}>{templates ? "Шаблон ограничен" : "Основание ограничено"}</p> : <article className="content-block" key={index}>
        {item.template && <><h4>{item.template.name} · версия шаблона {item.template.version}</h4><button type="button" disabled={action.busy} onClick={() => void action.run(() => saveExport(`/knowledge/template-versions/${item.template!.template_version_id}/download?mode=${mode}`, item.files?.[0]?.name || "template"), "Закреплённый шаблон скачан")}>Скачать закреплённый шаблон версии {item.template.version}</button></>}
        <h4>{item.document?.title || "Документ основания"}{item.anchor?.version ? ` · оригинал версии ${item.anchor.version}` : ""}</h4>
        {item.note && <p>{item.note}</p>}{item.blocks?.map(block => <blockquote className="content-text" key={block.id}>{block.text}</blockquote>)}
        {item.files?.map(file => <div className="file-line" key={file.id}><span>{file.name} · версия {file.version}</span><button type="button" disabled={action.busy} onClick={() => void action.run(() => saveDownload(file.id, file.name, mode === "viewer" ? "viewer" : "editor"), "Оригинал скачан")}>Скачать оригинал основания версии {file.version}</button></div>)}
        {item.document && <a href={`/${mode === "viewer" ? "view" : "work"}?${new URLSearchParams({document: item.document.id, ...(item.anchor?.file_id ? {tab: "content", file: item.anchor.file_id, ...(item.anchor.block_id ? {block: item.anchor.block_id, ordinal: String(item.anchor.ordinal || 0)} : {})} : {})})}`}>Открыть документ основания</a>}
      </article>)}
      {data.data?.items.length === 0 && <p>{templates ? "Шаблоны не добавлены." : "Основания не добавлены."}</p>}
    </div>}
  </section>;
}

export function ProcessView({process, version, readOnly, preview = false, selectedKey, onSelect}: {
  process: ProcessRecord; version: ProcessVersion; readOnly: boolean; preview?: boolean;
  selectedKey?: string; onSelect?: (key: string) => void;
}) {
  const [current, setCurrent] = useState(version.steps.find(step => step.is_start)?.key || version.steps[0]?.key || "");
  const selected = selectedKey ?? current;
  const step = version.steps.find(item => item.key === selected);
  const mode = readOnly ? "viewer" : "editor";
  const metadata = version.metadata_snapshot || process;
  function select(key: string) {setCurrent(key); onSelect?.(key);}
  return <section className="process-view">
    <div className="panel"><h2>{metadata.name || process.name} · версия {version.version}</h2><p>{metadata.purpose || metadata.description}</p><p className="muted">{metadata.department}{metadata.category ? " · " + metadata.category : ""} · действует с {version.effective_from}</p><p>Описание процесса помогает выполнить работу. Экземпляры выполнения и задачи здесь не создаются.</p></div>
    <ProcessGraph steps={version.steps.map(item => ({...item, terminal: item.is_terminal}))} transitions={version.transitions.map(item => ({key: item.key, from: item.from_step, to: item.to_step, label: item.restricted ? "Условие ограничено" : item.label, otherwise: item.is_else, condition: item.condition}))} selected={selected} start={version.steps.find(item => item.is_start)?.key || ""} onSelect={select}/>
    <label>Шаг процесса<select value={selected} onChange={event => select(event.target.value)}>{version.steps.map((item, index) => <option key={item.key} value={item.key}>{index + 1}. {item.title}</option>)}</select></label>
    {step && <section className="panel process-step-view"><h3>{step.title}</h3><p className="content-text">{step.instruction}</p><dl className="process-facts"><div><dt>Исполнитель</dt><dd>{step.executor_role || "Не указан"}</dd></div><div><dt>Подразделение</dt><dd>{step.department || "Не указано"}</dd></div><div><dt>Длительность</dt><dd>{step.duration_hours === null || step.duration_hours === undefined ? "Не указана" : `${step.duration_hours} ч`}</dd></div><div><dt>Срок</dt><dd>{step.deadline || "Не указан"}</dd></div><div><dt>Информационная система</dt><dd>{step.system || "Не указана"}</dd></div></dl>
      <h4>Входные данные</h4>{step.inputs.length ? <ul>{step.inputs.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>Не указаны.</p>}
      <h4>Результаты шага</h4>{step.outputs.length ? <ul>{step.outputs.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>Не указаны.</p>}
      {step.control_note && <p><strong>Контроль:</strong> {step.control_note}</p>}
      {preview ? <p className="muted">Основания и шаблоны проверяются сервером после сохранения черновика.</p> : <><ProcessBasis key={`${step.key}-basis`} versionId={version.id} step={step} mode={mode}/><ProcessBasis key={`${step.key}-templates`} versionId={version.id} step={step} mode={mode} templates/></>}
      {!preview && <RelatedKnowledge key={`${version.id}:${step.key}`} processVersionId={version.id} stepKey={step.key} readOnly={readOnly}/>}
      <h4>{step.is_terminal ? "Завершение процесса" : "Доступные следующие шаги"}</h4>
      <ul>{version.transitions.filter(item => item.from_step === step.key).map(item => <li key={item.key}><button type="button" className="text-button" onClick={() => select(item.to_step)}>{version.steps.find(next => next.key === item.to_step)?.title || "Следующий шаг"}</button> · {item.restricted ? "Условие ограничено" : item.is_else ? "Иначе" : item.label || (item.condition ? "По условию формы" : "Без условия")}</li>)}</ul>
    </section>}
  </section>;
}
