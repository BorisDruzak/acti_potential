import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DynamicForm } from "./DynamicForm";
import { sameFormValue } from "./formChanges";
import type { FormSchema, Values } from "./schema";

afterEach(cleanup);

it("compares actual form changes without treating cleared optional values or object key order as changes", () => {
  expect(sameFormValue({title: "Текст", note: null, people: []}, {people: undefined, title: "Текст"})).toBe(true);
  expect(sameFormValue({flag: false}, {flag: null})).toBe(false);
  expect(sameFormValue({rows: [{text: "А"}]}, {rows: [{text: "Б"}]})).toBe(false);
  expect(sameFormValue(["a", "b"], ["b", "a"])).toBe(false);
});

const schema: FormSchema = {sections: [{key: "main", title: "Основные сведения", fields: [
  {key: "title", label: "Наименование", type: "text", editable: true},
  {key: "note", label: "Примечание", type: "textarea", editable: true},
  {key: "rows", label: "Участники", type: "group", editable: true, children: [{key: "person", label: "Имя", type: "text", editable: true}]},
  {key: "original", label: "Оригинал", type: "file", editable: true},
]}]};
const saved: Values = {title: "Исходное", rows: [{person: "Первый"}]};
function Form({readOnly = false}: {readOnly?: boolean}) {
  const [values, setValues] = useState(saved);
  const [files, setFiles] = useState<ReadonlySet<string>>(new Set());
  return <><DynamicForm schema={schema} values={values} savedValues={saved} dirtyFileFields={files}
    mode={readOnly ? "view" : "edit"} roles={readOnly ? ["viewer"] : ["editor"]} onChange={setValues}
    onFile={field => setFiles(new Set([field]))}/><button onClick={() => {setValues(saved); setFiles(new Set());}}>Отменить</button></>;
}

it("marks the changed nested field and parent block, clears restored values and tracks a staged file", () => {
  const {container} = render(<Form/>);
  const name = screen.getByRole("textbox", {name: "Имя"});
  fireEvent.change(name, {target: {value: "Второй"}});
  expect(name.closest(".form-field")?.getAttribute("data-dirty")).toBe("true");
  expect(name.closest(".repeat-group")?.getAttribute("data-dirty")).toBe("true");
  expect(screen.getByRole("textbox", {name: "Наименование"}).closest(".form-field")?.getAttribute("data-dirty")).toBeNull();
  fireEvent.change(name, {target: {value: "Первый"}});
  expect(container.querySelectorAll("[data-dirty]")).toHaveLength(0);
  fireEvent.change(container.querySelector('input[type="file"]')!, {target: {files: [new File(["original"], "original.docx")]}});
  expect(container.querySelector('input[type="file"]')?.closest(".form-field")?.getAttribute("data-dirty")).toBe("true");
  fireEvent.click(screen.getByRole("button", {name: "Отменить"}));
  expect(container.querySelectorAll("[data-dirty]")).toHaveLength(0);
});

it("leaves reader values read-only with no unsaved-field affordances", () => {
  const {container} = render(<Form readOnly/>);
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(container.querySelector('input[type="file"]')).toBeNull();
  expect(container.querySelector("[data-dirty]")).toBeNull();
});

it("marks changed registration numbers rendered by the reservation control", () => {
  const numberSchema: FormSchema = {sections: [{key: "main", title: "Номер", fields: [{key: "number", label: "Номер", type: "text", system: "reg_number", number_series_id: "series", editable: true}]}]};
  const {container} = render(<DynamicForm schema={numberSchema} values={{number: "2"}} savedValues={{number: "1"}} mode="edit" roles={["editor"]}
    fieldContent={{number: id => <input id={id} value="2" readOnly/>}}/>);
  expect(container.querySelector(".form-field")?.getAttribute("data-dirty")).toBe("true");
});
