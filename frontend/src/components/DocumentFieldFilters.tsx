import { DynamicForm } from "../engine/DynamicForm";
import type { Field, ReferenceSet } from "../engine/schema";
export type FieldFilter = {key: string; op: string; value?: unknown; end?: unknown};
export function parseFieldFilters(raw: string | null): FieldFilter[] {
  try {
    const data = JSON.parse(raw || "{}");
    if (!data || typeof data !== "object" || Array.isArray(data)) return [];
    return Object.entries(data).map(([key, raw]) => {
      const spec = raw as {op?: string; value?: unknown} | null;
      if (spec && typeof spec === "object" && !Array.isArray(spec) && typeof spec.op === "string") {
        return {key, op: spec.op, value: spec.op === "between" && Array.isArray(spec.value) ? spec.value[0] : spec.value,
          end: spec.op === "between" && Array.isArray(spec.value) ? spec.value[1] : undefined};
      }
      return {key, op: "eq", value: raw};
    });
  } catch {return [];}
}
export function serializeFieldFilters(filters: FieldFilter[]) {
  return Object.fromEntries(filters.filter(filter => filter.key && (["present", "absent"].includes(filter.op) || (filter.value !== null && filter.value !== undefined && filter.value !== ""))).map(filter =>
    [filter.key, filter.op === "eq" ? filter.value : {op: filter.op, ...(!["present", "absent"].includes(filter.op) ? {value: filter.op === "between" ? [filter.value, filter.end] : filter.value} : {})}]));
}
export function DocumentFieldFilters({filters, fields, onChange, references, registryId, registryNames = {}, roles, readOnly}: {
  filters: FieldFilter[]; fields: Field[]; onChange: (filters: FieldFilter[]) => void; references: ReferenceSet[]; registryId: string; registryNames?: Record<string,string>; roles: string[]; readOnly: boolean;
}) {
  const update = (index: number, patch: Partial<FieldFilter>) => onChange(filters.map((filter, current) => current === index ? {...filter, ...patch} : filter));
  return <div className="document-field-filters" aria-label="Фильтры полей: все условия одновременно">
    <p className="muted">Все условия выполняются одновременно (И). До 20 разных полей.</p>
    {filters.map((filter, index) => {
      const field = fields.find(field => field.key === filter.key);
      return <fieldset key={index} className="document-field-filter"><legend>Условие {index + 1}</legend>
        <label>Поле фильтра {index + 1}<select value={filter.key} onChange={event => update(index, {key: event.target.value, op: "eq", value: null, end: ""})}>
          <option value="">Выберите поле</option>{!field && filter.key && <option value={filter.key}>Сохранённое поле: {filter.key}</option>}
          {fields.filter(field => field.key === filter.key || !filters.some(other => other.key === field.key)).map(field => <option key={field.key} value={field.key}>{field.label}</option>)}
        </select></label>
        <label>Сравнение {index + 1}<select value={filter.op} onChange={event => update(index, {op: event.target.value})}>
          <option value="eq">Равно</option><option value="ne">Не равно</option>
          {field && ["text", "textarea"].includes(field.type) && <option value="contains">Содержит</option>}
          {field && ["integer", "decimal", "date", "datetime"].includes(field.type) && <><option value="gte">Не меньше</option><option value="lte">Не больше</option><option value="between">В диапазоне</option></>}
          <option value="present">Заполнено</option><option value="absent">Не заполнено</option>
        </select></label>
        {field?.type === "boolean" && ["eq", "absent"].includes(filter.op) ? <label>Значение фильтра {index + 1}<select value={filter.op === "absent" ? "unspecified" : filter.value === true ? "true" : filter.value === false ? "false" : ""} onChange={event => update(index, event.target.value === "unspecified" ? {op: "absent", value: null} : {op: "eq", value: event.target.value === "" ? null : event.target.value === "true"})}>
          <option value="">Выберите значение</option><option value="true">Да</option><option value="false">Нет</option><option value="unspecified">Не указано</option>
        </select></label> : field && !["present", "absent"].includes(filter.op) && <div className="filter-value">
          <DynamicForm schema={{sections: [{key: "filter", title: "", fields: [{...field, label: `Значение фильтра ${index + 1}`, type: field.type === "multi_reference" ? "reference" : field.type, required: false, visible_when: null, required_when: null, editable: true, write_roles: [], modes: []}]}]}}
            values={{[field.key]: filter.value}} onChange={values => update(index, {value: values[field.key]})} mode="edit" roles={roles} registryId={registryId}
            references={!registryId ? references.map(reference=>({...reference,name:reference.name+(reference.registry_id && registryNames[reference.registry_id] ? ` · ${registryNames[reference.registry_id]}` : "")})) : references}
            referenceScope="available" displayMode={readOnly ? "viewer" : "editor"}/>
          {filter.op === "between" && <label>Верхняя граница {index + 1}<input type={field.type === "date" ? "date" : field.type === "datetime" ? "datetime-local" : "number"} step="any" value={String(filter.end ?? "")} onChange={event => update(index, {end: event.target.value})}/></label>}
        </div>}
        {!field && filter.key && <p className="muted">Сохранённое условие сохранено. Выберите его тип документа для редактирования.</p>}
        <button type="button" aria-label={`Удалить фильтр ${index + 1}`} onClick={() => onChange(filters.filter((_, current) => current !== index))}>Удалить условие</button>
      </fieldset>;
    })}
    <button type="button" disabled={filters.length >= 20 || !fields.some(field => !filters.some(filter => filter.key === field.key))} onClick={() => onChange([...filters, {key: "", op: "eq", value: null}])}>Добавить условие</button>
  </div>;
}
