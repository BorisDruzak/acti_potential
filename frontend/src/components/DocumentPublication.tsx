import type { CardState, FormSchema, PublicationTarget } from "../engine/schema";

export function publicationSection(schema: FormSchema) {
  return schema.sections.find(section => section.key === "publication" || section.title.trim().toLocaleLowerCase("ru") === "публикация")?.key;
}
export function mainInformationSection(schema: FormSchema) {
  const available = schema.sections.filter(section => section.key !== publicationSection(schema));
  return (available.find(section => ["main", "basic", "general"].includes(section.key) || /^(основные сведения|основное|реквизиты|общие сведения|основная информация)$/iu.test(section.title.trim())) || available[0])?.key;
}
export function DocumentCardState({state, onChange, published = false}: {state: CardState; onChange?: (state: CardState) => void; published?: boolean}) {
  return <label className="document-card-state">Состояние карточки{onChange ? <select value={state} onChange={event => onChange(event.target.value as CardState)}>
      {!published && <option value="draft">Черновик</option>}<option value="published">Опубликован</option>
    </select> : <span className="field-value">{state === "published" ? "Опубликован" : "Черновик"}</span>}</label>;
}
export function DocumentPublication({targets, onTargets}: {targets: PublicationTarget[]; onTargets?: (targets: PublicationTarget[]) => void}) {
  return <div className="document-publication">
    <fieldset><legend>Разместить в базе</legend>{([['internal', 'Внутренняя база'], ['shared', 'Общая база']] as const).map(([target, label]) => <label key={target} className="checkbox-label"><input type="checkbox" checked={targets.includes(target)} disabled={!onTargets} onChange={event => onTargets?.(event.target.checked ? [...targets, target] : targets.filter(value => value !== target))}/>{label}</label>)}</fieldset>
    {onTargets && <small>Читатели видят только опубликованные карточки в общей базе. Публикация не изменяет статус правового акта.</small>}
  </div>;
}
