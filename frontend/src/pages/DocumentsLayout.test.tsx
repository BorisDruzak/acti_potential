import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { Documents } from "./Documents";
import type { FormSchema } from "../engine/schema";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); localStorage.clear(); window.history.replaceState(null, "", "/work");});
function setup(mixedVersions = false, sectionNavigation = false, role = "editor", create = false) {
  let revision = 1;
  const schema: FormSchema = {sections: [{key: "main", title: "Основное", fields: [
    {key: "caption", label: "Наименование", type: "text", system: "title", editable: true},
    {key: "x_kind", label: "Категория", type: "text", filterable: true},
    {key: "x_topic", label: "Содержание", type: "text", filterable: true},
    {key: "x_owner", label: "Куратор", type: "user"},
    {key: "private", label: "Закрыто", type: "text", read_roles: ["admin"]},
  ]}], list_view: {columns: [{key: "x_kind", label: "Вид документа"}, {key: "x_topic", label: "Тема"}, {key: "x_owner", label: "Ответственный исполнитель"}]}};
  if (sectionNavigation) schema.sections.push(
    {key: "publication", title: "Публикация", fields: [{key: "published", label: "Место публикации", type: "text", editable: true}]},
    {key: "control", title: "Контроль исполнения", fields: [{key: "result", label: "Результат", type: "text", editable: true}]},
    {key: "restricted", title: "Закрытый раздел", fields: [{key: "secret", label: "Скрытое поле", type: "text", read_roles: ["admin"]}]},
  );
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "layout-user", roles: [role], csrf_token: "csrf"};
    else if (url.endsWith("/registries")) data = [{id: "r", name: "Реестр", document_type_ids: ["type"], permissions: ["read", "update", ...(create ? ["create"] : [])]}];
    else if (url.includes("/document-types/type/versions")) {
      const newer = JSON.parse(JSON.stringify(schema));
      newer.sections[0].fields.find((field: {key: string}) => field.key === "x_topic").key = "new_topic";
      newer.list_view.columns.find((column: {key: string}) => column.key === "x_topic").key = "new_topic";
      data = [{id: "schema", state: "published", document_type_id: "type", version: 1, schema},
        ...(mixedVersions ? [{id: "schema2", state: "published", document_type_id: "type", version: 2, schema: newer}] : [])];
    }
    else if (url.endsWith("/document-types")) data = [{id: "type", name: "Приказ"}];
    else if (url.endsWith("/search")) data = {items: ["a", "b"].map((id, index) => ({document: {id, document_type_id: "type", schema_version_id: "schema", title: index ? "Бета" : "Альфа", permissions: ["read", "update", "download"], revision: 1, card_state: index ? "published" : "draft", reg_number: "Р-" + (index + 1), reg_date: "2026-10-16", values: {caption: "Альфа", x_kind: "Распоряжение", x_topic: "Благоустройство", x_owner: "opaque-id"}, display_values: {x_kind: "Распоряжение", x_topic: "Благоустройство", x_owner: "Сотрудник"}}, matches: []})), total: 40, next_cursor: "page-two", page_size: 20};
    else if (url.endsWith("/documents") && init?.method === "POST") data = {id: "a"};
    else if (url.includes("/documents/a/content")) data = {items: [], total: 0, page: 1, page_size: 20};
    else if (url.includes("/documents/a/search-index")) data = {ready: true};
    else if (url.includes("/documents/a")) {
      if (init?.method === "PUT") revision++;
      data = {id: "a", registry_id: "r", revision, current_version: revision, title: "Альфа", reg_number: "Р-1", values: {caption: "Альфа"}, schema, permissions: ["read", "update"], files: []};
    }
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() {return useAuth().user ? <Documents/> : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return fetch;
}
const mode = (name: string) => screen.getByRole("radio", {name});
const firstRow = () => screen.findByRole("row", {name: /Выбрать документ «Альфа»/});

it("loads the legacy legal-act status from an existing saved search", async () => {
  window.history.replaceState(null, "", "/work?status=other");
  localStorage.setItem("acti.views.v1.layout-user.editor", JSON.stringify([{name: "Действующие", registry: "", query: "status=active"}]));
  const requests = setup(); await firstRow();
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  fireEvent.click(screen.getByText("Сохранённые поиски", {exact: true}));
  fireEvent.click(screen.getByRole("button", {name: "Действующие"}));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(requests.mock.calls.some(([url, init]) => url.endsWith("/search") && JSON.parse(String(init?.body)).status === "active")).toBe(true));
});

it("keeps applied results and the edited card when advanced search is cancelled", async () => {
  const requests = setup();
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  fireEvent.change(screen.getByLabelText("Наименование"), {target: {value: "Черновик карточки"}});
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  const drawer = within(screen.getByRole("dialog", {name: "Расширенный поиск"}));
  const count = requests.mock.calls.filter(([url]) => url.endsWith("/search")).length;
  fireEvent.change(drawer.getByLabelText("Регистрационный номер"), {target: {value: "999"}});
  fireEvent.click(drawer.getByRole("button", {name: "Отмена"}));
  expect(requests.mock.calls.filter(([url]) => url.endsWith("/search"))).toHaveLength(count);
  expect((screen.getByLabelText("Наименование") as HTMLInputElement).value).toBe("Черновик карточки");
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  expect((within(screen.getByRole("dialog")).getByLabelText("Регистрационный номер") as HTMLInputElement).value).toBe("");
});

function registrationFixture(state: "draft" | "published", rejectPublication = false) {
 const fetch=setup(),base=fetch.getMockImplementation()!;
 const schema: FormSchema={sections:[{key:"main",title:"Основное",fields:[
  {key:"caption",label:"Наименование",type:"text",system:"title",editable:true},
  {key:"number",label:"Регистрационный номер",type:"text",system:"reg_number",editable:true},
  {key:"date",label:"Дата регистрации",type:"date",system:"reg_date",editable:true},
 ]}]};
 let revision=1,values={caption:"Альфа",number:"Draft/42",date:"2026-10-18"};
 fetch.mockImplementation(async(url,init)=>{
  if(/\/documents\/a(?:\?|$)/.test(url)){
   if(init?.method==="PUT"){
    const request=JSON.parse(String(init.body));
    if(rejectPublication&&request.card_state==="published")return new Response(JSON.stringify({error:{code:"invalid_field",message:"Публикация не прошла проверку"}}),{status:422});
    state=request.card_state;revision++;values={...values,...request.values};
   }
   return new Response(JSON.stringify({id:"a",registry_id:"r",document_type_id:"type",schema_version_id:"schema",revision,current_version:0,
    values,schema,card_state:state,publication_targets:["internal"],number_entry_id:null,number_locked:false,permissions:["read","update"],files:[]}));
  }
  return base(url,init);
 });
 return fetch;
}

it("locks both published registration fields even without a journal entry while other fields remain editable",async()=>{
 const fetch=registrationFixture("published");
 fireEvent.click(await firstRow());fireEvent.click(await screen.findByRole("button",{name:"Редактировать карточку"}));
 const form=document.querySelector(".document-card-form")as HTMLFormElement;
 const number=within(form).getByLabelText("Регистрационный номер")as HTMLInputElement;
 expect(number.readOnly).toBe(true);
 const date=within(form).getByLabelText("Дата регистрации")as HTMLInputElement;
 expect(date.tagName!=="INPUT"||date.readOnly||date.disabled).toBe(true);
 expect(screen.queryByRole("button",{name:"Опубликовать"})).toBeNull();
 fireEvent.change(screen.getByLabelText("Наименование"),{target:{value:"Другие изменения"}});
 fireEvent.submit(document.querySelector(".document-card-form")!);
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>url.endsWith("/documents/a")&&init?.method==="PUT")).toBe(true));
 const body=JSON.parse(String(fetch.mock.calls.find(([url,init])=>url.endsWith("/documents/a")&&init?.method==="PUT")![1]?.body));
 expect(body.card_state).toBe("published");expect(body.values).toEqual({caption:"Другие изменения"});
});

it.each([false,true])("keeps save separate from publication and retains unsaved fields (publish=%s)",async publish=>{
 const fetch=registrationFixture("draft");
 fireEvent.click(await firstRow());fireEvent.click(await screen.findByRole("button",{name:"Редактировать карточку"}));
 const form=document.querySelector(".document-card-form")as HTMLFormElement;
 fireEvent.change(within(form).getByLabelText("Регистрационный номер"),{target:{value:"Edited/42"}});
 fireEvent.change(within(form).getByLabelText("Дата регистрации"),{target:{value:"2026-10-19"}});
 expect(within(form).queryByRole("combobox",{name:"Состояние карточки"})).toBeNull();
 const button=screen.getByRole("button",{name:publish?"Опубликовать":"Сохранить карточку"});
 fireEvent.click(button);
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>url.endsWith("/documents/a")&&init?.method==="PUT")).toBe(true));
 const body=JSON.parse(String(fetch.mock.calls.find(([url,init])=>url.endsWith("/documents/a")&&init?.method==="PUT")![1]?.body));
 expect(body).toMatchObject({card_state:publish?"published":"draft",values:{number:"Edited/42",date:"2026-10-19"}});
});

it("does not make a later ordinary save publish a draft after a failed publication",async()=>{
 const fetch=registrationFixture("draft",true);
 fireEvent.click(await firstRow());fireEvent.click(await screen.findByRole("button",{name:"Редактировать карточку"}));
 fireEvent.change(screen.getByLabelText("Наименование"),{target:{value:"Несохранённое"}});
 fireEvent.click(screen.getByRole("button",{name:"Опубликовать"}));
 await screen.findByText("Публикация не прошла проверку");
 expect((screen.getByLabelText("Наименование")as HTMLInputElement).value).toBe("Несохранённое");
 fireEvent.click(screen.getByRole("button",{name:"Сохранить карточку"}));
 await waitFor(()=>expect(fetch.mock.calls.filter(([url,init])=>url.endsWith("/documents/a")&&init?.method==="PUT")).toHaveLength(2));
 const bodies=fetch.mock.calls.filter(([url,init])=>url.endsWith("/documents/a")&&init?.method==="PUT").map(([,init])=>JSON.parse(String(init?.body)));
 expect(bodies.map(body=>body.card_state)).toEqual(["published","draft"]);
});

it.each([false,true])("creates a draft by default or publishes through the separate create-form action (publish=%s)",async publish=>{
 const fetch=setup(false,false,"editor",true);
 const toolbar=await screen.findByRole("group",{name:"Вид и сортировка документов"});
 fireEvent.click(within(toolbar).getByRole("button",{name:"Создать документ"}));
 const creation=document.querySelector(".document-create")as HTMLElement;
 fireEvent.change(within(creation).getByLabelText("Тип документа"),{target:{value:"type"}});
 fireEvent.change(await within(creation).findByLabelText("Наименование"),{target:{value:"Новый документ"}});
 fireEvent.click(within(creation).getByRole("button",{name:publish?"Опубликовать":"Создать документ"}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>url.endsWith("/documents")&&init?.method==="POST")).toBe(true));
 const body=JSON.parse(String(fetch.mock.calls.find(([url,init])=>url.endsWith("/documents")&&init?.method==="POST")![1]?.body));
 expect(body.card_state).toBe(publish?"published":"draft");
});

it("allows correcting a duplicate manual registration number without treating it as a stale document revision", async () => {
 const fetch=setup(),base=fetch.getMockImplementation()!;let writes=0;
 const schema: FormSchema={sections:[{key:"main",title:"Основное",fields:[
  {key:"caption",label:"Наименование",type:"text",system:"title",editable:true},
  {key:"reg_number",label:"Регистрационный номер",type:"text",system:"reg_number",editable:true},
 ]}]};
 fetch.mockImplementation(async(url,init)=>{
  if(/\/documents\/a(?:\?|$)/.test(url)){
   if(init?.method==="PUT"&&++writes===1)return new Response(JSON.stringify({error:{code:"number_conflict",message:"Регистрационный номер уже занят"}}),{status:409});
   return new Response(JSON.stringify({id:"a",registry_id:"r",document_type_id:"type",schema_version_id:"schema",revision:1,current_version:0,values:{caption:"Альфа",reg_number:"FREE-1"},schema,permissions:["read","update"],files:[]}));
  }
  return base(url,init);
 });
 fireEvent.click(await firstRow());fireEvent.click(await screen.findByRole("button",{name:"Редактировать карточку"}));
 const form=document.querySelector(".document-card-form")as HTMLFormElement;
 fireEvent.change(within(form).getByLabelText("Регистрационный номер"),{target:{value:"TAKEN"}});
 fireEvent.submit(form);await screen.findByText("Регистрационный номер уже занят");
 fireEvent.change(within(form).getByLabelText("Регистрационный номер"),{target:{value:"FREE-2"}});
 fireEvent.submit(form);await waitFor(()=>expect(writes).toBe(2));
 const requests=fetch.mock.calls.filter(([url,init])=>url.endsWith("/documents/a")&&init?.method==="PUT");
 expect(JSON.parse(String(requests[1][1]?.body)).values.reg_number).toBe("FREE-2");
});

it("reserves a registration number inside the unsaved form and attaches its journal entry on creation", async () => {
 const fetch=setup(false,false,"editor",true),base=fetch.getMockImplementation()!;
 const schema: FormSchema={sections:[{key:"main",title:"Основное",fields:[
  {key:"caption",label:"Наименование",type:"text",system:"title",editable:true},
  {key:"reg_number",label:"Регистрационный номер",type:"text",system:"reg_number",editable:true},
 ]}]};
 fetch.mockImplementation(async(url,init)=>{
  if(url.includes("/document-types/type/versions"))return new Response(JSON.stringify([{id:"schema",document_type_id:"type",version:1,state:"published",schema}]));
  if(url.endsWith("/registries/r/next-number"))return new Response(JSON.stringify({number:"42",number_entry_id:"entry42",series_id:"series"}));
  return base(url,init);
 });
 const toolbar=await screen.findByRole("group",{name:"Вид и сортировка документов"});
 fireEvent.click(await within(toolbar).findByRole("button",{name:"Создать документ"}));
 const creation=document.querySelector(".document-create")as HTMLElement;
 fireEvent.change(within(creation).getByLabelText("Тип документа"),{target:{value:"type"}});
 await within(creation).findByLabelText("Наименование");
 fireEvent.change(within(creation).getByLabelText("Наименование"),{target:{value:"Сохранить черновик"}});
 fireEvent.click(within(creation).getByRole("button",{name:"Присвоить номер"}));
 await waitFor(()=>expect((within(creation).getByLabelText("Регистрационный номер")as HTMLInputElement).value).toBe("42"));
 fireEvent.submit(creation.querySelector("form")!);
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>url.endsWith("/documents")&&init?.method==="POST")).toBe(true));
 const body=JSON.parse(String(fetch.mock.calls.find(([url,init])=>url.endsWith("/documents")&&init?.method==="POST")![1]?.body));
 expect(body).toMatchObject({number_entry_id:"entry42",values:{reg_number:"42",caption:"Сохранить черновик"},card_state:"draft"});
});

it.each([["edit", false], ["create", false], ["edit", true], ["create", true]] as const)("waits for every inline participant before submitting the %s document and includes the new selections (simultaneous=%s)", async (operation, simultaneous) => {
  const fetch = setup(false, false, "editor", true);
  const base = fetch.getMockImplementation()!;
  const pending = new Map<string, (response: Response) => void>();
  const schema: FormSchema = {sections: [{key: "main", title: "Основное", fields: [
    {key: "caption", label: "Наименование", type: "text", editable: true},
    {key: "custom_executors", label: "Исполнители", type: "multi_reference", reference_code: "executors", editable: true},
    {key: "custom_signers", label: "Подписанты", type: "multi_reference", reference_code: "signers", editable: true},
  ]}]};
  fetch.mockImplementation(async (url, init) => {
    if (/\/references(?:\?|$)/.test(url)) return new Response(JSON.stringify(["executors", "signers"].map(code => ({id: code, code, name: code, registry_id: "r"}))));
    if (/\/references\/.*\/items/.test(url)) return new Response(JSON.stringify([{id: "old", label: "Уже в справочнике", active: true}]));
    if (url.includes("/document-types/type/versions")) return new Response(JSON.stringify([{id: "schema", state: "published", document_type_id: "type", version: 1, schema}]));
    if (/\/documents\/a\?/.test(url)) return new Response(JSON.stringify({id: "a", registry_id: "r", schema_version_id: "schema", revision: 1, current_version: 0, values: {caption: "Альфа"}, schema, permissions: ["read", "update"], files: []}));
    if (url.endsWith("/registries/r/people")) return new Promise<Response>(resolve => { pending.set(JSON.parse(String(init?.body)).field_key, resolve); });
    return base(url, init);
  });
  let form: HTMLFormElement;
  const saveCalls = () => fetch.mock.calls.filter(([url, init]) => operation === "edit"
    ? url.endsWith("/documents/a") && init?.method === "PUT"
    : url.endsWith("/documents") && init?.method === "POST");
  if (operation === "edit") {
    fireEvent.click(await firstRow());
    fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
    form = document.querySelector(".document-card-form") as HTMLFormElement;
  } else {
    const toolbar = await screen.findByRole("group", {name: "Вид и сортировка документов"});
    fireEvent.click(await within(toolbar).findByRole("button", {name: "Создать документ"}));
    const creation = document.querySelector(".document-create") as HTMLElement;
    fireEvent.change(within(creation).getByLabelText("Тип документа"), {target: {value: "type"}});
    await within(creation).findByLabelText("Наименование");
    form = creation.querySelector(".document-card-form") as HTMLFormElement;
  }
  fireEvent.change(within(form).getByLabelText("Наименование"), {target: {value: "Несохранённое название"}});
  for (const [fieldLabel, name] of [["Исполнители", "Новый исполнитель"], ["Подписанты", "Новый подписант"]]) {
    const input = within(form).getByRole("combobox", {name: fieldLabel});
    fireEvent.focus(input);
    await screen.findByRole("option", {name: "Уже в справочнике"});
    fireEvent.change(input, {target: {value: name}});
    act(() => {
      screen.getByRole<HTMLButtonElement>("button", {name: `Создать «${name}»`}).click();
      form.dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
    });
  }
  const submit = (operation === "edit" ? within(document.querySelector(".document-card-top") as HTMLElement) : within(form)).getByRole<HTMLButtonElement>("button", {name: operation === "edit" ? "Сохранить карточку" : "Создать документ"});
  expect(submit.disabled).toBe(true);
  fireEvent.submit(form);
  expect(saveCalls()).toHaveLength(0);
  const resolveExecutor = () => pending.get("custom_executors")!(new Response(JSON.stringify({item: {id: "new-executor", label: "Новый исполнитель", active: true}, created: true}), {status: 201}));
  const resolveSigner = () => pending.get("custom_signers")!(new Response(JSON.stringify({item: {id: "new-signer", label: "Новый подписант", active: true}, created: true}), {status: 201}));
  if (simultaneous) {
    await act(async () => { resolveExecutor(); resolveSigner(); });
  } else {
    await act(async () => resolveExecutor());
    expect(within(form).getByRole("button", {name: "Убрать Новый исполнитель"})).toBeTruthy();
    expect(submit.disabled).toBe(true);
    fireEvent.submit(form);
    expect(saveCalls()).toHaveLength(0);
    await act(async () => resolveSigner());
  }
  expect(within(form).getByRole("button", {name: "Убрать Новый исполнитель"})).toBeTruthy();
  expect(within(form).getByRole("button", {name: "Убрать Новый подписант"})).toBeTruthy();
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  await waitFor(() => expect(saveCalls()).toHaveLength(1));
  expect(JSON.parse(String(saveCalls()[0][1]?.body)).values).toEqual({caption: "Несохранённое название", custom_executors: ["new-executor"], custom_signers: ["new-signer"]});
});

it("creates a participant in the actual card registry with CSRF while all registries are selected and retains the card draft", async () => {
  const fetch = setup();
  const base = fetch.getMockImplementation()!;
  const schema: FormSchema = {sections: [{key: "main", title: "Основное", fields: [
    {key: "caption", label: "Наименование", type: "text", editable: true},
    {key: "custom_people", label: "Исполнители", type: "multi_reference", reference_code: "executors", editable: true},
  ]}]};
  fetch.mockImplementation(async (url, init) => {
    if (/\/references(?:\?|$)/.test(url)) return new Response(JSON.stringify([{id: "people-r", code: "executors", name: "Исполнители", registry_id: "r"}]));
    if (url.includes("/references/people-r/items")) return new Response(JSON.stringify([{id: "old", label: "Уже выбранный", active: true}]));
    if (url.endsWith("/registries/r/people")) return new Response(JSON.stringify({item: {id: "new", label: "Новый участник", active: true}, created: true}), {status: 201});
    if (/\/documents\/a\?/.test(url)) return new Response(JSON.stringify({id: "a", registry_id: "r", schema_version_id: "card-schema", revision: 1, current_version: 0, title: "Альфа", values: {caption: "Альфа", custom_people: ["old"]}, schema, permissions: ["read", "update"], files: []}));
    return base(url, init);
  });
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  fireEvent.change(screen.getByLabelText("Наименование"), {target: {value: "Несохранённая карточка"}});
  await screen.findByRole("button", {name: "Убрать Уже выбранный"});
  const participantInput = screen.getByRole("combobox", {name: "Исполнители"});
  fireEvent.focus(participantInput);
  fireEvent.change(participantInput, {target: {value: "Новый участник"}});
  fireEvent.click(screen.getByRole("button", {name: "Создать «Новый участник»"}));
  expect(await screen.findByRole("button", {name: "Убрать Новый участник"})).toBeTruthy();
  expect(screen.getByRole("button", {name: "Убрать Уже выбранный"})).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("Наименование").value).toBe("Несохранённая карточка");
  expect(screen.getByRole<HTMLSelectElement>("combobox", {name: "Реестр"}).value).toBe("");
  const [, request] = fetch.mock.calls.find(([url]) => url.endsWith("/registries/r/people"))!;
  expect(request?.method).toBe("POST");
  expect(new Headers(request?.headers).get("X-CSRF-Token")).toBe("csrf");
  expect(JSON.parse(String(request?.body))).toEqual({schema_version_id: "card-schema", field_key: "custom_people", label: "Новый участник", document_id: "a"});
  expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/documents/a") && init?.method === "PUT")).toBe(false);
});

it("uses each selected card's registry references while the result filter stays on all registries", async () => {
  const fetch = setup();
  const base = fetch.getMockImplementation()!;
  const schema: FormSchema = {sections: [{key: "main", title: "Основные сведения", fields: [
    {key: "people", label: "Исполнитель", type: "multi_reference", reference_code: "executors", editable: true},
  ]}]};
  fetch.mockImplementation(async (url, init) => {
    if (url.endsWith("/registries")) return new Response(JSON.stringify(["r", "r2"].map(id => ({id, name: id, document_type_ids: ["type"], permissions: ["read", "update"]}))));
    if (/\/references(?:\?|$)/.test(url)) return new Response(JSON.stringify(["r2", "r"].map(id => ({id: "people-" + id, code: "executors", name: id, registry_id: id}))));
    if (url.includes("/references/people-")) return new Response(JSON.stringify([{id: "person-" + (url.includes("people-r2/") ? "r2" : "r"), label: url.includes("people-r2/") ? "Сотрудник второго реестра" : "Сотрудник первого реестра"}]));
    if (/\/documents\/[ab]\?/.test(url)) {
      const second = url.includes("/documents/b?");
      return new Response(JSON.stringify({id: second ? "b" : "a", registry_id: second ? "r2" : "r", revision: 1, current_version: 0, title: second ? "Бета" : "Альфа", values: {}, schema, permissions: ["read", "update"], files: []}));
    }
    return base(url, init);
  });
  expect((await screen.findByRole("combobox", {name: "Реестр"}) as HTMLSelectElement).value).toBe("");
  for (const [title, expected, other] of [["Альфа", "Сотрудник первого реестра", "Сотрудник второго реестра"], ["Бета", "Сотрудник второго реестра", "Сотрудник первого реестра"]]) {
    fireEvent.click(await screen.findByRole("row", {name: new RegExp(`Выбрать документ «${title}»`)}));
    await screen.findByRole("heading", {name: title, level: 1});
    fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
    fireEvent.focus(screen.getByRole("combobox", {name: "Исполнитель"}));
    expect(await screen.findByRole("option", {name: expected})).toBeTruthy();
    expect(screen.queryByRole("option", {name: other})).toBeNull();
    expect((screen.getByRole("combobox", {name: "Реестр"}) as HTMLSelectElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", {name: "Отмена изменений"}));
  }
});

it("keeps the four working tabs and permission-aware section navigation without duplicate header metadata", async () => {
  setup(false, true);
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Редактировать карточку"});
  const card = document.querySelector(".document-card") as HTMLElement;
  expect(Array.from(card.querySelectorAll(":scope > .tabs button"), item => item.textContent)).toEqual(["Карточка", "Файлы и версии", "История изменений", "Содержание"]);
  const header = card.querySelector(".document-card-top > .toolbar")!;
  expect(header.textContent).not.toContain("UUID:");
  expect(header.textContent).not.toContain("редакция");
  expect(header.textContent).toContain("Р-1");
  const nav = within(card).getByRole("navigation", {name: "Разделы формы"});
  expect(within(nav).getAllByRole("link").map(link => link.textContent)).toEqual(["Основное", "Публикация", "Контроль исполнения"]);
  for (const link of within(nav).getAllByRole("link")) expect(document.getElementById(link.getAttribute("href")!.slice(1))).toBeTruthy();
  expect(within(card).queryByText("Скрытое поле")).toBeNull();
  expect(within(card).getByText("Альфа", {selector: ".field-value"})).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", {name: "Редактировать карточку"}));
  const input = screen.getByLabelText("Наименование");
  fireEvent.change(input, {target: {value: "Черновик раздела"}});
  window.history.pushState(null, "", window.location.pathname + window.location.search + nav.querySelector("a")!.getAttribute("href"));
  fireEvent(window, new PopStateEvent("popstate"));
  expect(screen.getByLabelText("Наименование")).toBe(input);
  expect((input as HTMLInputElement).value).toBe("Черновик раздела");
});

it("uses one three-position control, selects on click and opens full card on double-click or Enter", async () => {
  setup();
  expect(await screen.findByRole("radio", {name: "Вместе"})).toHaveProperty("ariaChecked", "true");
  expect(screen.queryByRole("button", {name: "Открыть карточку"})).toBeNull();
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Редактировать карточку"});
  expect(mode("Вместе").getAttribute("aria-checked")).toBe("true");
  fireEvent.click(mode("Таблица"));
  fireEvent.doubleClick(await firstRow());
  expect(mode("Карточка").getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("button", {name: "К списку"}));
  expect(mode("Таблица").getAttribute("aria-checked")).toBe("true");
  fireEvent.keyDown(await firstRow(), {key: "Enter"});
  expect(mode("Карточка").getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("button", {name: "Показать список документов"}));
  expect(mode("Вместе").getAttribute("aria-checked")).toBe("true");
});
it("keeps one mounted card and its draft, active tab, selection, query and loaded results across modes", async () => {
  const fetch = setup();
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  fireEvent.change(screen.getByLabelText("Наименование"), {target: {value: "Черновик"}});
  fireEvent.change(screen.getByRole("searchbox", {name: "Поиск"}), {target: {value: "Запрос"}});
  fireEvent.scroll(screen.getByRole("region", {name: "Список документов"}));
  const requests = fetch.mock.calls.filter(([url]) => url.includes("/documents/a?")).length;
  for (const name of ["Таблица", "Карточка", "Вместе"]) fireEvent.click(mode(name));
  expect((screen.getByLabelText("Наименование") as HTMLInputElement).value).toBe("Черновик");
  expect((screen.getByRole("searchbox", {name: "Поиск"}) as HTMLInputElement).value).toBe("Запрос");
  expect(screen.queryByText(/Страница/)).toBeNull();
  expect(fetch.mock.calls.filter(([url]) => url.includes("/documents/a?")).length).toBe(requests);
  fireEvent.click(screen.getByRole("button", {name: "Файлы и версии"}));
  fireEvent.click(mode("Карточка")); fireEvent.click(mode("Вместе"));
  expect(screen.getByRole("button", {name: "Файлы и версии"}).className).toContain("active");
});
it("renders configured columns and independent legal act status from each document's configured schema and sorts via the existing API", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("radio", {name: "Таблица"}));
  await screen.findAllByText("Благоустройство");
  expect(screen.getAllByRole("columnheader")).toHaveLength(10);
  expect(screen.getByRole("columnheader", {name: "Состояние карточки"})).toBeTruthy();
  expect(screen.queryByRole("button", {name: "Сортировать: Состояние карточки"})).toBeNull();
  expect(within(await firstRow()).getByRole("gridcell", {name: "Состояние карточки: Черновик"})).toBeTruthy();
  const row = within(await firstRow());
  expect(row.getByText("Сотрудник")).toBeTruthy();
  expect(row.queryByText("opaque-id")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Сортировать: Наименование документа"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).some(([, init]) => JSON.parse(String(init?.body)).sort === "title")).toBe(true));
  expect(localStorage.getItem("acti.documents.layout.v1.layout-user.editor")).toContain("table");
});

it("does not offer a page-dependent custom sort when another published version uses a different field", async () => {
  window.history.replaceState(null, "", "/work?document_type_id=type");
  const fetch = setup(true);
  fireEvent.click(await screen.findByRole("radio", {name: "Таблица"}));
  await screen.findAllByText("Благоустройство");
  expect(screen.queryByRole("button", {name: "Сортировать: Тема"})).toBeNull();
  expect(screen.getByRole("button", {name: "Сортировать: Вид документа"})).toBeTruthy();
  fireEvent.scroll(screen.getByRole("region", {name: "Список документов"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).length).toBeGreaterThan(1));
  expect(screen.queryByRole("button", {name: "Сортировать: Тема"})).toBeNull();
});

it("keeps quick search compact and export independent of the advanced drawer", async () => {
  setup(); await firstRow();
  const search = screen.getByRole("search", {name: "Поиск документов"});
  expect(within(search).getByRole("combobox", {name: "Реестр"})).toHaveProperty("value", "");
  expect(within(search).queryByLabelText("Регистрационный номер")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByText("Экспорт", {exact: true}));
  expect(screen.getByRole("button", {name: "Экспорт CSV"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  expect(within(screen.getByRole("dialog")).getByLabelText("Регистрационный номер")).toBeTruthy();
});

it("searches all permitted registries without asking the user to pick just one", async () => {
  const fetch = setup();
  await firstRow();
  expect(screen.queryByText("Выберите реестр для просмотра документов.")).toBeNull();
  expect(screen.getByText("По всем доступным реестрам")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Реестр", {exact: true}), {target: {value: "r"}});
  fireEvent.change(screen.getByLabelText("Реестр", {exact: true}), {target: {value: ""}});
  fireEvent.change(screen.getByRole("searchbox", {name: "Поиск"}), {target: {value: "Общий запрос"}});
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).some(([, init]) => {
    const body = JSON.parse(String(init?.body)); return body.query === "Общий запрос" && body.registry_ids.length === 0;
  })).toBe(true));
  const row = within(await firstRow());
  expect(row.getByRole("gridcell", {name: "Наименование документа: Альфа"})).toBeTruthy();
  expect(row.getByRole("gridcell", {name: "Регистрационный номер: Р-1"})).toBeTruthy();
  expect(row.getByRole("gridcell", {name: "Дата регистрации: 2026-10-16"})).toBeTruthy();
});

it.each(["editor", "viewer"])("keeps registry scope in quick search and preserves the query in card mode for %s", async role => {
  const fetch = setup(false, false, role);
  fireEvent.click(await firstRow());
  fireEvent.click(mode("Карточка"));
  const search = screen.getByRole("search", {name: "Поиск документов"});
  const registry = within(search).getByRole("combobox", {name: "Реестр"});
  fireEvent.change(within(search).getByRole("searchbox", {name: "Поиск"}), {target: {value: "Общий запрос"}});
  fireEvent.change(registry, {target: {value: "r"}});
  expect(within(search).getByRole("searchbox", {name: "Поиск"})).toHaveProperty("value", "Общий запрос");
  fireEvent.click(within(search).getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).some(([, init]) => {
    const body = JSON.parse(String(init?.body)); return body.query === "Общий запрос" && body.registry_ids.join() === "r";
  })).toBe(true));
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  const drawer = screen.getByRole("dialog");
  expect(within(drawer).getByLabelText("Реестр документов")).toHaveProperty("value", "r");
  fireEvent.change(within(drawer).getByLabelText("Реестр документов"), {target: {value: ""}});
  fireEvent.click(within(drawer).getByRole("button", {name: "Отмена"}));
  expect(registry).toHaveProperty("value", "r");
  expect(within(search).getByRole("searchbox", {name: "Поиск"})).toHaveProperty("value", "Общий запрос");
});

it("places saved filters below search and creation beside the view switch with All registries selected", async () => {
  const fetch = setup(false, false, "editor", true);
  await firstRow();
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  const search = document.querySelector(".search-drawer-form")!;
  const views = document.querySelector(".search-saved-views")!;
  expect(search.compareDocumentPosition(views) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", {name: "Отмена"}));
  const toolbar = screen.getByRole("group", {name: "Вид и сортировка документов"});
  expect(within(screen.getByRole("search", {name: "Поиск документов"})).getByLabelText("Реестр", {exact: true})).toHaveProperty("value", "");
  fireEvent.click(mode("Таблица"));
  fireEvent.click(within(toolbar).getByRole("button", {name: "Создать документ"}));
  expect(screen.getByLabelText("Реестр документа")).toHaveProperty("value", "r");
  fireEvent.change(screen.getByLabelText("Тип документа"), {target: {value: "type"}});
  const creation = document.querySelector(".document-create") as HTMLElement;
  const form = within(creation);
  const create = await form.findByRole("button", {name: "Создать документ"});
  expect(form.getByText("Черновик",{exact:true})).toBeTruthy();
  expect(form.getByLabelText("Внутренняя база")).toHaveProperty("checked", true);
  expect(form.getByLabelText("Общая база")).toHaveProperty("checked", false);
  fireEvent.click(form.getByLabelText("Общая база"));
  expect(create).toBeTruthy();
  fireEvent.click(form.getByRole("button",{name:"Опубликовать"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/documents") && init?.method === "POST" && JSON.parse(String(init.body)).card_state === "published" && JSON.parse(String(init.body)).publication_targets.join(",") === "internal,shared")).toBe(true));
  await waitFor(() => expect(mode("Вместе").getAttribute("aria-checked")).toBe("true"));
  fireEvent.click(await screen.findByRole("button", {name: "К списку"}));
  expect(mode("Таблица").getAttribute("aria-checked")).toBe("true");
});

it("keeps lifecycle in Main information and targets in the existing Publication section", async () => {
  const fetch = setup(false, true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const card = document.querySelector(".document-card") as HTMLElement;
  const publication = within(card).getByRole("heading", {name: "Публикация"}).closest("section")!;
  expect(within(publication).getByLabelText("Место публикации")).toBeTruthy();
  const main = within(card).getByRole("heading", {name: "Основное"}).closest("section")!;
  expect(within(publication).queryByLabelText("Состояние карточки")).toBeNull();
  expect(within(main).getByText("Черновик",{exact:true})).toBeTruthy();
  fireEvent.click(within(publication).getByLabelText("Общая база"));
  fireEvent.click(within(card).getByRole("button", {name: "Опубликовать"}));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT" && JSON.parse(String(init.body)).card_state === "published" && JSON.parse(String(init.body)).publication_targets.includes("shared"))).toBe(true));
});

it("treats user as a reader even when document grants include update and create", async () => {
  const fetch = setup(false, false, "user", true);
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Содержание"});
  expect(screen.queryByRole("button", {name: "Редактировать карточку"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Создать документ"})).toBeNull();
  expect(screen.queryByRole("option", {name: "Черновик"})).toBeNull();
  expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/search") && JSON.parse(String(init?.body)).mode === "viewer" && JSON.parse(String(init?.body)).card_state === "published")).toBe(true);
});

it("creates inside the card pane, guards dirty navigation and restores the mounted selected draft on cancel", async () => {
  const fetch = setup(false, true, "editor", true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const previousInput = screen.getByLabelText("Наименование") as HTMLInputElement;
  fireEvent.change(previousInput, {target: {value: "Черновик выбранной карточки"}});
  const query = screen.getByRole("searchbox", {name: "Поиск"});
  fireEvent.change(query, {target: {value: "Несохранённый запрос"}});
  const pane = screen.getByRole("region", {name: "Список документов"});
  pane.scrollTop = 85;
  fireEvent.click(mode("Таблица"));
  const toolbar = screen.getByRole("group", {name: "Вид и сортировка документов"});
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(within(toolbar).getByRole("button", {name: "Создать документ"}));
  expect(document.querySelector(".document-create")).toBeNull();
  confirm.mockReturnValue(true);
  const requests = fetch.mock.calls.filter(([url]) => url.endsWith("/search")).length;
  fireEvent.click(within(toolbar).getByRole("button", {name: "Создать документ"}));
  expect(mode("Вместе")).toHaveProperty("ariaChecked", "true");
  expect(screen.getByRole("searchbox", {name: "Поиск"})).toBe(query);
  expect(screen.getByRole("region", {name: "Список документов"})).toBe(pane);
  expect(previousInput.isConnected).toBe(true);
  const creation = document.querySelector(".document-create") as HTMLElement;
  expect(screen.getByRole("region", {name: "Содержимое карточки"}).contains(creation)).toBe(true);
  fireEvent.change(within(creation).getByLabelText("Тип документа"), {target: {value: "type"}});
  const newInput = await within(creation).findByLabelText("Наименование");
  fireEvent.change(newInput, {target: {value: "Новая несохранённая карточка"}});
  for (const name of ["Таблица", "Карточка", "Вместе"]) fireEvent.click(mode(name));
  expect(within(creation).getByLabelText("Наименование")).toBe(newInput);
  confirm.mockReturnValue(false);
  fireEvent.click(screen.getByRole("row", {name: /Выбрать документ «Бета»/}));
  expect(document.querySelector(".document-create")).toBe(creation);
  expect(window.dispatchEvent(new Event("acti:before-workspace-navigation", {cancelable: true}))).toBe(false);
  expect(window.dispatchEvent(new Event("beforeunload", {cancelable: true}))).toBe(false);
  fireEvent.click(within(creation).getByRole("button", {name: "Отмена создания"}));
  expect(document.querySelector(".document-create")).toBe(creation);
  confirm.mockReturnValue(true);
  fireEvent.click(within(creation).getByRole("button", {name: "Отмена создания"}));
  expect(document.querySelector(".document-create")).toBeNull();
  expect(mode("Таблица")).toHaveProperty("ariaChecked", "true");
  expect(previousInput.isConnected).toBe(true);
  expect(previousInput.value).toBe("Черновик выбранной карточки");
  expect((query as HTMLInputElement).value).toBe("Несохранённый запрос");
  expect(pane.scrollTop).toBe(85);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).length).toBe(requests);
});

it.each(["row", "create"])("guards the retained selected draft before replacing it through %s from pristine creation", async operation => {
  const fetch = setup(false, true, "editor", true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const previousInput = screen.getByLabelText("Наименование") as HTMLInputElement;
  fireEvent.change(previousInput, {target: {value: "Несохранённая выбранная карточка"}});
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(screen.getByRole("group", {name: "Вид и сортировка документов"})).getByRole("button", {name: "Создать документ"}));
  const creation = document.querySelector(".document-create") as HTMLElement;
  fireEvent.change(within(creation).getByLabelText("Тип документа"), {target: {value: "type"}});
  await within(creation).findByLabelText("Наименование");
  confirm.mockClear(); confirm.mockReturnValue(false);
  if (operation === "row") fireEvent.click(screen.getByRole("row", {name: /Выбрать документ «Бета»/}));
  else fireEvent.click(within(creation).getByRole("button", {name: "Создать документ"}));
  expect(confirm).toHaveBeenCalledOnce();
  expect(document.querySelector(".document-create")).toBe(creation);
  expect(previousInput.isConnected).toBe(true);
  expect(previousInput.value).toBe("Несохранённая выбранная карточка");
  expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/documents") && init?.method === "POST")).toBe(false);
  expect(fetch.mock.calls.some(([url]) => url.includes("/documents/b?"))).toBe(false);
  fireEvent.click(within(creation).getByRole("button", {name: "Отмена создания"}));
  expect(previousInput.isConnected).toBe(true);
  expect(screen.getByLabelText("Наименование")).toBe(previousInput);
});

it.each([false, true])("restores URL and mounted draft when history replacement is cancelled (creation=%s)", async creating => {
  setup(false, true, "editor", true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  let form = document.querySelector(".document-card") as HTMLElement;
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  if (creating) {
    fireEvent.click(within(screen.getByRole("group", {name: "Вид и сортировка документов"})).getByRole("button", {name: "Создать документ"}));
    form = document.querySelector(".document-create") as HTMLElement;
    fireEvent.change(within(form).getByLabelText("Тип документа"), {target: {value: "type"}});
    await within(form).findByLabelText("Наименование");
  }
  const input = within(form).getByLabelText("Наименование") as HTMLInputElement;
  fireEvent.change(input, {target: {value: "Черновик для истории"}});
  const original = window.location.pathname + window.location.search;
  confirm.mockClear(); confirm.mockReturnValue(false);
  const anchor = within(form).getByRole("navigation", {name: "Разделы формы"}).querySelector("a")!.getAttribute("href")!;
  window.history.pushState(null, "", original + anchor);
  fireEvent(window, new PopStateEvent("popstate"));
  expect(confirm).not.toHaveBeenCalled();
  expect(within(form).getByLabelText("Наименование")).toBe(input);
  const prior = window.location.pathname + window.location.search + window.location.hash;
  window.history.pushState(null, "", "/work?document=b");
  fireEvent(window, new PopStateEvent("popstate"));
  expect(confirm).toHaveBeenCalledOnce();
  expect(window.location.pathname + window.location.search + window.location.hash).toBe(prior);
  expect(input.isConnected).toBe(true);
  expect(input.value).toBe("Черновик для истории");
});

it("restores the content tab on same-document history even without a file or block target", async () => {
  setup();
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Редактировать карточку"});
  window.history.pushState(null, "", "/work?document=a&tab=content");
  fireEvent(window, new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByRole("button", {name: "Содержание"}).className).toContain("active"));
  window.history.pushState(null, "", "/work?document=a");
  fireEvent(window, new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByRole("button", {name: "Карточка"}).className).toContain("active"));
});


it("exports exactly the checkbox selection while retaining the dirty open card on right-click", async () => {
  const fetch = setup();
  vi.stubGlobal("URL", class extends URL {static createObjectURL() {return "blob:selection";} static revokeObjectURL() {}});
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const input = screen.getByLabelText("Наименование") as HTMLInputElement;
  fireEvent.change(input, {target: {value: "Мой черновик"}});
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("checkbox", {name: "Выделить документ «Альфа»"}));
  fireEvent.click(screen.getByRole("checkbox", {name: "Выделить документ «Бета»"}));
  fireEvent.contextMenu(screen.getByRole("row", {name: /Выбрать документ «Бета»/}), {clientX: 300, clientY: 200});
  expect(screen.getByRole("menu", {name: "Действия с выбранными документами: 2"})).toBeTruthy();
  expect(screen.getByLabelText("Наименование")).toBe(input);
  expect(input.value).toBe("Мой черновик"); expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("menuitem", {name: "Экспорт выбранных CSV"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/documents/export-selection") && init?.method === "POST" && JSON.parse(String(init.body)).document_ids.join(",") === "a,b")).toBe(true));
  expect(screen.getByLabelText("Наименование")).toBe(input);
});

it("requires a deletion reason and submits the confirmed card revision", async () => {
  const fetch = setup();
  fireEvent.contextMenu(await firstRow(), {clientX: 150, clientY: 100});
  fireEvent.click(screen.getByRole("menuitem", {name: "Удалить выбранные документы"}));
  const dialog = await screen.findByRole("dialog", {name: "Удалить выбранные документы?"});
  const confirm = within(dialog).getByRole("button", {name: "Подтвердить удаление"});
  expect(confirm).toHaveProperty("disabled", true);
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  fireEvent.change(within(dialog).getByLabelText("Причина удаления"), {target: {value: "Дубликат карточки"}});
  fireEvent.click(confirm);
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/documents/a") && init?.method === "DELETE" && JSON.parse(String(init.body)).revision === 1 && JSON.parse(String(init.body)).reason === "Дубликат карточки")).toBe(true));
});

it("restores all field predicates from the URL and removes only the requested condition", async () => {
  const filters = {x_kind: "Распоряжение", x_topic: {op: "contains", value: "Благоустройство"}};
  window.history.replaceState(null, "", "/work?document_type_id=type&reg_number=17&reg_date=2026-10-17&status=active&filters=" + encodeURIComponent(JSON.stringify(filters)));
  const fetch = setup();
  await firstRow();
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  expect(await screen.findByLabelText("Поле фильтра 1")).toHaveProperty("value", "x_kind");
  expect(screen.getByLabelText("Поле фильтра 2")).toHaveProperty("value", "x_topic");
  fireEvent.click(within(screen.getByRole("dialog", {name: "Расширенный поиск"})).getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/search") && Object.keys(JSON.parse(String(init?.body)).filters).length === 2)).toBe(true));
  fireEvent.click(screen.getByRole("button", {name: "Удалить активное условие 1"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => {
    if (!url.endsWith("/search")) return false;
    const body = JSON.parse(String(init?.body)); return body.filters.x_topic?.value === "Благоустройство" && !body.filters.x_kind && body.reg_number === "17" && body.date_from === "2026-10-17" && body.status === "active";
  })).toBe(true));
});

it("preserves a dirty form and shows the named remote revision before an explicit reload", async () => {
  const fetch = setup();
  const base = fetch.getMockImplementation()!; let remoteRevision = 1;
  fetch.mockImplementation(async (url, init) => url.includes("/collaboration")
    ? new Response(JSON.stringify({revision: remoteRevision, updated_at: "2026-10-17T12:00:00Z", updated_by: {id: "other", display_name: "Другой редактор"}, participants: [{id: "other", display_name: "Другой редактор", editing: true}]}))
    : base(url, init));
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Редактировать карточку"});
  vi.useFakeTimers();
  try {
    fireEvent.click(screen.getByRole("button", {name: "Редактировать карточку"}));
    await act(async () => {});
    const input = screen.getByLabelText("Наименование") as HTMLInputElement;
    fireEvent.change(input, {target: {value: "Не терять мои изменения"}});
    remoteRevision = 2;
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(screen.getByLabelText("Наименование")).toBe(input);
    expect(input.value).toBe("Не терять мои изменения");
    expect(screen.getByRole("button", {name: "Сохранить карточку"})).toHaveProperty("disabled", true);
    expect(screen.getByText(/Последняя редакция 2/).textContent).toContain("Другой редактор");
    expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", {name: "Загрузить актуальную редакцию"}));
    expect(confirm).toHaveBeenCalledOnce(); expect(input.isConnected).toBe(true);
  } finally {cleanup(); vi.useRealTimers();}
});
