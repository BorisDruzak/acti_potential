import type { CardState, FormSchema, PublicationTarget } from "../engine/schema";

export function publicationSection(schema: FormSchema) {
  return schema.sections.find(section => section.key === "publication" || section.title.trim().toLocaleLowerCase("ru") === "публикация")?.key;
}
export function DocumentPublication({state, targets, onState, onTargets, published = false}: {state: CardState; targets: PublicationTarget[]; onState?: (state: CardState) => void; onTargets?: (targets: PublicationTarget[]) => void; published?: boolean}) {
  return <div className="document-publication">
    <label>Состояние карточки{onState ? <select value={state} onChange={event => onState(event.target.value as CardState)}>
      {!published && <option value="draft">Черновик</option>}<option value="published">Опубликован</option>
    </select> : <span className="field-value">{state === "published" ? "Опубликован" : "Черновик"}</span>}</label>
    <fieldset><legend>Разместить в базе</legend>{([['internal', 'Внутренняя база'], ['shared', 'Общая база']] as const).map(([target, label]) => <label key={target} className="checkbox-label"><input type="checkbox" checked={targets.includes(target)} disabled={!onTargets} onChange={event => onTargets?.(event.target.checked ? [...targets, target] : targets.filter(value => value !== target))}/>{label}</label>)}</fieldset>
    {onTargets && <small>Читатели видят только опубликованные карточки в общей базе. Публикация не изменяет статус правового акта.</small>}
  </div>;
}
