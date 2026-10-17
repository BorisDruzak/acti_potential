import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { ReferencesAdmin } from "./Admin";
import { ReferenceItems } from "./ReferenceItems";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function setup(element: React.ReactNode) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const data = url.endsWith("/users/me") ? { id: "admin", roles: ["admin"], csrf_token: "csrf" }
      : url.endsWith("/registries") ? [{ id: "registry", name: "Отдельный реестр" }]
      : url.endsWith("/references") ? init?.method === "POST" ? { id: "new", code: "custom", name: "Свой справочник", registry_id: "registry" } : []
      : url.endsWith("/items") ? [{ id: "person", label: "Сотрудник", active: true, metadata: { position: "Юрист", department: "Отдел", private: "retain" } }]
      : {};
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() { return useAuth().user ? element : null; }
  render(<AuthProvider><Ready /></AuthProvider>);
  return fetch;
}

it("edits participant position and department while retaining existing metadata", async () => {
  const fetch = setup(<ReferenceItems reference={{ id: "people", code: "executors", name: "Исполнители" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Изменить: Сотрудник" }));
  const editor = screen.getByRole("button", { name: "Сохранить значение" }).closest("form")!;
  fireEvent.change(within(editor).getByLabelText("Должность"), { target: { value: "Советник" } });
  fireEvent.change(within(editor).getByLabelText("Подразделение"), { target: { value: "Управление" } });
  fireEvent.click(within(editor).getByRole("button", { name: "Сохранить значение" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
  expect(JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === "PATCH")![1]?.body)).metadata)
    .toEqual({ position: "Советник", department: "Управление", private: "retain" });
});

it("creates a reference set for an explicitly selected separate registry", async () => {
  const fetch = setup(<ReferencesAdmin />);
  fireEvent.click(await screen.findByRole("button", { name: "Создать справочник" }));
  fireEvent.change(screen.getByLabelText("Название справочника"), { target: { value: "Свой справочник" } });
  fireEvent.change(screen.getByLabelText("Код справочника"), { target: { value: "custom" } });
  const scope = screen.getByLabelText("Область справочника") as HTMLSelectElement;
  expect(scope.value).toBe("");
  expect(scope.required).toBe(true);
  fireEvent.change(scope, { target: { value: "registry" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Создать справочник" })[1]);
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/references") && init?.method === "POST")).toBe(true));
  expect(JSON.parse(String(fetch.mock.calls.find(([url, init]) => url.endsWith("/references") && init?.method === "POST")![1]?.body)))
    .toEqual({ name: "Свой справочник", code: "custom", registry_id: "registry" });
});
