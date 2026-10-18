import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../App";
import { AuthProvider } from "../auth/AuthProvider";
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});
function setup(roles: string[], mustChange = false) {
  let revision = 1;
  let uploaded = false;
  const fetch = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      let data: unknown = {};
      if (url.endsWith("/users/me"))
        data = {
          id: "u",
          display_name: "Тест",
          roles,
          csrf_token: "csrf",
          must_change_password: mustChange,
        };
      else if (url.includes("/registries"))
        data = [
          {
            id: "r",
            name: "Тестовый реестр",
            code: "test",
            document_type_ids: ["t"],
            permissions: ["read", "create", "update", "upload", "download"],
          },
        ];
      else if (url.includes("/document-types/t/versions"))
        data = [
          {
            id: "s",
            document_type_id: "t",
            version: 1,
            state: "published",
            schema: {
              sections: [
                {
                  key: "main",
                  title: "Основное",
                  fields: [
                    {
                      key: "publish",
                      label: "Публиковать",
                      type: "boolean",
                      filterable: true,
                    },
                  ],
                },
              ],
            },
          },
        ];
      else if (url.includes("/document-types"))
        data = [{ id: "t", code: "test", name: "Тестовый тип" }];
      else if (url.includes("/references")) data = [];
      else if (url.endsWith("/search"))
        data = {
          items: [
            {
              id: "d",
              title: "Тестовый документ",
              revision: 1,
              reg_number: "TEST-1",
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        };
      else if (url.includes("/documents/d")) {
        if (init?.method === "PUT") {
          revision += 1;
          if (JSON.parse(String(init.body)).files?.length) uploaded = true;
        }
        if (url.includes("/files?") && init?.method === "POST") {
          revision += 1;
          uploaded = true;
        }
        data = {
          id: "d",
          title: "Тестовый документ",
          registry_id: "r",
          revision,
          values: { title: "Тестовый документ" },
          permissions: ["read", "update", "upload", "download"],
          files: uploaded
            ? [
                {
                  id: "f",
                  name: "example.txt",
                  version: 1,
                  field_key: "original",
                  size: 7,
                  created_at: "2026-10-15T00:00:00Z",
                },
              ]
            : [],
          schema: {
            sections: [
              {
                key: "main",
                title: "Карточка",
                fields: [
                  { key: "title", label: "Название", type: "text" },
                  { key: "original", label: "Оригинал", type: "file" },
                ],
              },
            ],
          },
        };
      }
      if (url.endsWith("/search")) {
        const batch = data as {items: unknown[]}; batch.items = batch.items.map(document => ({document, matches: []}));
      }
      return Promise.resolve(new Response(JSON.stringify(data)));
    });
  vi.stubGlobal("fetch", fetch);
  render(
    <AuthProvider>
      <App />
    </AuthProvider>,
  );
  return fetch;
}
it("forces initial password change before loading any workspace configuration", async () => {
  const fetch = setup(["admin"], true);
  await screen.findByRole("heading", { name: "Установите свой пароль" });
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/v1/users/me"]);
  expect(screen.queryByRole("navigation")).toBeNull();
});
it("reader route exposes no mutation controls even when returned permissions include them", async () => {
  window.history.replaceState(null, "", "/view");
  const fetch = setup(["viewer"]);
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "r"}});
  fireEvent.click(
    await screen.findByRole("row", {name: /Выбрать документ/}),
  );
  await screen.findByRole("heading", {name: "Тестовый документ"});
  expect(screen.queryByRole("button", { name: "Создать документ" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Редактировать карточку" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Сохранить карточку" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Название" })).toBeNull();
  expect(
    screen.queryByLabelText("Добавить файл или следующую версию"),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Пользователи" })).toBeNull();
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([url]) => url === "/api/v1/documents/d?mode=viewer",
      ),
    ).toBe(true),
  );
});
it("viewer cannot open administration by typing its route", async () => {
  window.history.replaceState(null, "", "/admin");
  setup(["viewer"]);
  await screen.findByRole("heading", { name: "Документы" });
  await waitFor(() => expect(window.location.pathname).toBe("/view"));
  expect(screen.queryByRole("button", { name: "Типы документов" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Справочники" })).toBeNull();
});
it("opens references from the editor workspace without exposing administration", async () => {
  window.history.replaceState(null, "", "/work");
  const fetch=setup(["editor"]);
  fireEvent.click(await screen.findByRole("button", {name:"Справочники"}));
  await screen.findByRole("heading", {name:"Справочники"});
  expect(screen.queryByRole("button", {name:"Пользователи"})).toBeNull();
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>url==="/api/v1/references?manage=true")).toBe(true));
});
it("opens an editable card without submitting and retains direct controls after cancellation", async () => {
  window.history.replaceState(null, "", "/work");
  const fetch = setup(["editor"]);
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "r"}});
  fireEvent.click(
    await screen.findByRole("row", {name: /Выбрать документ/}),
  );
  const saveButton = (await screen.findByRole("button", {
    name: "Сохранить карточку",
  })) as HTMLButtonElement;
  expect(screen.queryByRole("button", { name: "Редактировать карточку" })).toBeNull();
  expect(saveButton.type).toBe("submit");
  const title = screen.getByRole("textbox", { name: "Название" }) as HTMLInputElement;
  expect(title.disabled).toBe(false);
  expect(title.value).toBe(
    "Тестовый документ",
  );
  expect(
    fetch.mock.calls.filter(([, init]) => init?.method === "PUT"),
  ).toHaveLength(0);
  fireEvent.change(title, { target: { value: "Несохранённое название" } });
  fireEvent.click(screen.getByRole("button", { name: "Отмена изменений" }));
  expect((screen.getByRole("textbox", { name: "Название" }) as HTMLInputElement).value).toBe("Тестовый документ");
  expect(screen.getByRole("button", { name: "Сохранить карточку" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Редактировать карточку" })).toBeNull();
  expect(
    fetch.mock.calls.filter(([, init]) => init?.method === "PUT"),
  ).toHaveLength(0);
});
it("atomically saves a file selected during editing with the updated card values", async () => {
  window.history.replaceState(null, "", "/work");
  const fetch = setup(["editor"]);
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "r"}});
  fireEvent.click(
    await screen.findByRole("row", {name: /Выбрать документ/}),
  );
  await screen.findByRole("button", { name: "Сохранить карточку" });
  const file = new File(["example"], "example.txt", { type: "text/plain" });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode("example").buffer,
  });
  fireEvent.change(screen.getByLabelText("Оригинал"), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить карточку" }));
  await screen.findByText("example.txt · версия 1");
  const update = fetch.mock.calls.find(
    ([url, init]) => url === "/api/v1/documents/d" && init?.method === "PUT",
  );
  expect(update).toBeTruthy();
  const body = JSON.parse(String(update![1].body));
  expect(body.revision).toBe(1);
  expect(body.files).toEqual([
    {
      field_key: "original",
      name: "example.txt",
      mime: "text/plain",
      content_base64: "ZXhhbXBsZQ==",
    },
  ]);
  expect(fetch.mock.calls.some(([url]) => url.includes("/files?"))).toBe(false);
});
it("boolean extra filter uses typed control and sends false rather than dropping it", async () => {
  window.history.replaceState(null, "", "/view");
  const fetch = setup(["viewer"]);
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "r"}});
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  fireEvent.change(screen.getByLabelText("Тип"), { target: { value: "t" } });
  fireEvent.click(await screen.findByRole("button", {name: "Добавить условие"}));
  fireEvent.change(screen.getByLabelText("Поле фильтра 1"), {
    target: { value: "publish" },
  });
  fireEvent.change(screen.getByLabelText("Значение фильтра 1"), {
    target: { value: "false" },
  });
  fireEvent.click(within(screen.getByRole("dialog", {name: "Расширенный поиск"})).getByRole("button", {name: "Найти"}));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([url, init]) =>
          url.endsWith("/search") &&
          JSON.stringify(JSON.parse(String(init?.body)).filters) === '{"publish":false}',
      ),
    ).toBe(true),
  );
});

it("records aggregate search-to-open timing without the query or personal dimensions", async () => {
  window.history.replaceState(null, "", "/view");
  const fetch = setup(["viewer"]);
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "r"}});
  fireEvent.change(screen.getByLabelText("Поиск"), {target: {value: "Частный поисковый текст"}});
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  fireEvent.click(await screen.findByRole("row", {name: /Выбрать документ/}));
  await screen.findByRole("heading", {name: "Тестовый документ"});
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/usage/retrieval"))).toHaveLength(2));
  const bodies = fetch.mock.calls.filter(([url]) => url.endsWith("/usage/retrieval")).map(([, init]) => JSON.parse(String(init.body)));
  expect(bodies[0]).toEqual({registry_id: "r", operation: "search", duration_ms: 0});
  expect(bodies[1]).toEqual({registry_id: "r", operation: "time_to_open", duration_ms: expect.any(Number)});
  expect(JSON.stringify(bodies)).not.toContain("Частный поисковый текст");
});

it("guards sidebar navigation while inline creation has an unsaved publication draft", async () => {
  window.history.replaceState(null, "", "/work");
  setup(["editor"]);
  fireEvent.click(await screen.findByRole("button", {name: "Создать документ"}));
  const creation = document.querySelector(".document-create") as HTMLElement;
  fireEvent.change(within(creation).getByLabelText("Тип документа"), {target: {value: "t"}});
  fireEvent.click(await within(creation).findByLabelText("Общая база"));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", {name: "Мой пароль"}));
  expect(document.querySelector(".document-create")).toBe(creation);
  expect(confirm).toHaveBeenCalled();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", {name: "Мой пароль"}));
  expect(document.querySelector(".document-create")).toBeNull();
  expect(await screen.findByLabelText("Текущий пароль")).toBeTruthy();
});

it("keeps inline creation and the previous route when workspace history navigation is cancelled", async () => {
  window.history.replaceState(null, "", "/work");
  setup(["editor"]);
  fireEvent.click(await screen.findByRole("button", {name: "Создать документ"}));
  const creation = document.querySelector(".document-create") as HTMLElement;
  fireEvent.change(within(creation).getByLabelText("Тип документа"), {target: {value: "t"}});
  fireEvent.click(await within(creation).findByLabelText("Общая база"));
  const previous = window.location.pathname + window.location.search;
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  window.history.pushState(null, "", "/view");
  fireEvent(window, new PopStateEvent("popstate"));
  expect(confirm).toHaveBeenCalledOnce();
  expect(window.location.pathname + window.location.search).toBe(previous);
  expect(document.querySelector(".document-create")).toBe(creation);
  expect(screen.getByRole("combobox", {name: "Рабочее пространство"})).toHaveProperty("value", "work");
});
