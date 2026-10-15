import { DynamicForm } from "../engine/DynamicForm";
import type { Condition, Field, FormSchema, ReferenceSet } from "../engine/schema";

function initialOperand(field?: Field): unknown {
  const choices = (field?.constraints as Record<string, unknown> | undefined)?.choices;
  if (Array.isArray(choices) && choices.length) return choices[0];
  return field?.type === "boolean" ? false : field?.type === "integer" ? 0 : field?.type === "decimal" ? "0" : field?.type === "multi_reference" ? [] : "";
}
function Operand({field, value, onChange, references, registryId}: {field: Field; value: unknown; onChange: (value: unknown) => void; references: ReferenceSet[]; registryId: string}) {
  const choices = (field.constraints as Record<string, unknown> | undefined)?.choices;
  if (Array.isArray(choices)) return <label>Значение условия<select value={choices.findIndex(choice => choice === value)} onChange={event => onChange(choices[Number(event.target.value)])}><option value={-1}>Выберите значение</option>{choices.map((choice, index) => <option key={index} value={index}>{choice === null ? "Не указано" : typeof choice === "boolean" ? choice ? "Да" : "Нет" : String(choice)}</option>)}</select></label>;
  const input: Field = {...field, key: "operand", label: "Значение условия", required: false, required_when: null, visible_when: null, editable: true, read_roles: [], write_roles: [], modes: [], constraints: {...field.constraints}, number_series_id: null};
  return <DynamicForm schema={{sections: [{key: "operand", title: "", fields: [input]}]}} values={{operand: value}} onChange={values => onChange(values.operand)} mode="edit" roles={["admin", "editor"]} references={references} registryId={registryId}/>;
}
export function ProcessConditionEditor({schema, condition, onChange, references, registryId}: {
  schema?: FormSchema | null; condition: Condition | null; onChange: (condition: Condition) => void; references: ReferenceSet[]; registryId: string;
}) {
  const fields = schema?.sections.flatMap(section => section.fields) || [];
  const field = fields.find(item => item.key === condition?.field);
  const membership = condition && ["in", "not_in"].includes(condition.op);
  return <fieldset className="process-condition"><legend>Условие перехода</legend>
    <label>Поле условия<select value={condition?.field || ""} onChange={event => {const next = fields.find(item => item.key === event.target.value); onChange({field: event.target.value, op: next && ["file", "group"].includes(next.type) ? "present" : "eq", value: next && ["file", "group"].includes(next.type) ? undefined : initialOperand(next)});}}><option value="">Выберите поле опубликованной формы</option>{fields.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
    {!schema && <p className="muted">Сначала закрепите опубликованную форму в исходном шаге перехода.</p>}
    {field && condition && <>
      <label>Оператор условия<select value={condition.op} onChange={event => {const op = event.target.value as Condition["op"]; onChange(["present", "absent"].includes(op) ? {field: condition.field, op} : {field: condition.field, op, value: ["in", "not_in"].includes(op) ? [initialOperand(field)] : initialOperand(field)});}}>
        {!["file", "group"].includes(field.type) && <><option value="eq">Равно</option><option value="ne">Не равно</option><option value="in">В списке</option><option value="not_in">Вне списка</option></>}
        <option value="present">Заполнено</option><option value="absent">Не заполнено</option>
      </select></label>
      {!["present", "absent"].includes(condition.op) && (membership ? <>
        {(Array.isArray(condition.value) ? condition.value : []).map((value, index) => <div className="process-operand" key={index}><Operand field={field} value={value} onChange={next => onChange({...condition, value: (condition.value as unknown[]).map((item, position) => position === index ? next : item)})} references={references} registryId={registryId}/><button type="button" aria-label={`Удалить значение условия ${index + 1}`} onClick={() => onChange({...condition, value: (condition.value as unknown[]).filter((_, position) => position !== index)})}>Убрать значение</button></div>)}
        <button type="button" onClick={() => onChange({...condition, value: [...(Array.isArray(condition.value) ? condition.value : []), initialOperand(field)]})}>Добавить значение условия</button>
      </> : <Operand field={field} value={condition.value} onChange={value => onChange({...condition, value})} references={references} registryId={registryId}/>)}
    </>}
  </fieldset>;
}
