import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { SchemaBuilder } from "./SchemaBuilder";
import type { Field, SchemaVersion } from "./schema";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function setup(fields: Field[], state = "draft") {
  const requests: { method: string; body: { schema: SchemaVersion["schema"] } }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "PUT") requests.push({ method: options.method, body: JSON.parse(String(options.body)) });
    return new Response(JSON.stringify(url.endsWith("/users/me")
      ? { id: "admin", display_name: "Admin", roles: ["admin"], csrf_token: "csrf" }
      : {}));
  }));
  const version: SchemaVersion = {
    id: "schema", document_type_id: "type", version: 1, state,
    schema: { sections: [{ key: "main", title: "Основное", fields }] },
  };
  function Ready() {
    return useAuth().user ? <SchemaBuilder version={version} references={[{ id: "dictionary", code: "dict", name: "Справочник теста" }]} onSaved={() => {}} /> : null;
  }
  render(<AuthProvider><Ready /></AuthProvider>);
  await screen.findByText("Конструктор формы");
  return requests;
}

const title: Field = { key: "title", label: "Название", type: "text" };

it("blocks saving an invalid visible integer default even after selecting another field", async () => {
  const requests = await setup([{ key: "count", label: "Количество", type: "integer", default: 5 }, title]);
  fireEvent.click(screen.getByRole("button", { name: /Количество.*count/ }));
  fireEvent.change(screen.getByLabelText("Значение по умолчанию"), { target: { value: "5x" } });
  expect(screen.getByRole("alert").textContent).toMatch(/по умолчанию/);
  fireEvent.click(screen.getByRole("button", { name: /Название.*title/ }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить черновик" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/Количество/));
  expect(requests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: /Количество.*count/ }));
  fireEvent.change(screen.getByLabelText("Значение по умолчанию"), { target: { value: "7" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить черновик" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].body.schema.sections[0].fields[0].default).toBe(7);
});

it("removes a hidden dictionary binding when changing reference to text", async () => {
  const requests = await setup([{ key: "choice", label: "Выбор", type: "reference", reference_set_id: "dictionary" }]);
  fireEvent.click(screen.getByRole("button", { name: /Выбор.*choice/ }));
  fireEvent.change(screen.getByLabelText("Тип поля"), { target: { value: "text" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить черновик" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].body.schema.sections[0].fields[0]).not.toHaveProperty("reference_set_id");
});

it("clears incompatible group children, constraints, and defaults on type change", async () => {
  const requests = await setup([{ key: "rows", label: "Строки", type: "group", children: [title], constraints: { min_items: 1 }, default: [{ title: "Текст" }] }]);
  fireEvent.click(screen.getByRole("button", { name: /Строки.*rows/ }));
  fireEvent.change(screen.getByLabelText("Тип поля"), { target: { value: "integer" } });
  expect((screen.getByLabelText("Значение по умолчанию") as HTMLInputElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить черновик" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  const field = requests[0].body.schema.sections[0].fields[0];
  expect(field).not.toHaveProperty("children");
  expect(field).not.toHaveProperty("default");
  expect(field.constraints || {}).toEqual({});
});

it("keeps the last display mode enabled and explains the restriction", async () => {
  await setup([title]);
  fireEvent.click(screen.getByRole("button", { name: /Название.*title/ }));
  fireEvent.click(screen.getByLabelText("Редактор"));
  expect((screen.getByLabelText("Читатель") as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/хотя бы один режим/i)).toBeTruthy();
});

it("allows inspecting published field properties while keeping them read only", async () => {
  await setup([title], "published");
  fireEvent.change(screen.getByLabelText("Свойства опубликованного поля"), { target: { value: "0:0" } });
  const input = screen.getByLabelText("Название поля") as HTMLInputElement;
  expect(input.value).toBe("Название");
  expect(input.closest("fieldset")?.disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "Сохранить черновик" })).toBeNull();
});

it("shows the actual comparison value when enabling a condition", async () => {
  await setup([title, { key: "flag", label: "Флаг", type: "boolean" }]);
  fireEvent.click(screen.getByRole("button", { name: /Название.*title/ }));
  const condition = screen.getByText("Условие видимости").closest("fieldset")!;
  fireEvent.change(within(condition).getByLabelText("Поле условия"), { target: { value: "flag" } });
  expect((within(condition).getByLabelText(/^Значение/) as HTMLInputElement).value).toBe("true");
});
