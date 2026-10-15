import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { SchemaBuilder } from "./SchemaBuilder";
import { type SchemaVersion } from "./schema";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const version: SchemaVersion = {
  id: "schema",
  document_type_id: "type",
  version: 1,
  state: "draft",
  schema: {
    sections: [
      {
        key: "main",
        title: "Основное",
        fields: [{ key: "title", label: "Название", type: "text" }],
      },
    ],
  },
};
function Ready({
  current,
  saved,
}: {
  current: SchemaVersion;
  saved: () => void;
}) {
  return useAuth().user ? (
    <SchemaBuilder version={current} references={[]} onSaved={saved} />
  ) : (
    <p>Загрузка</p>
  );
}
function setup(current = version) {
  const fetch = vi.fn().mockImplementation((url: string) =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          url.endsWith("/users/me")
            ? {
                id: "user",
                display_name: "Тест",
                roles: ["admin"],
                csrf_token: "csrf",
              }
            : {},
        ),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const saved = vi.fn();
  render(
    <AuthProvider>
      <Ready current={current} saved={saved} />
    </AuthProvider>,
  );
  return { fetch, saved };
}
it("creates a field visually, configures its stable key, and publishes its draft", async () => {
  const { fetch, saved } = setup();
  fireEvent.change(await screen.findByLabelText("Добавляемый тип"), {
    target: { value: "textarea" },
  });
  fireEvent.click(screen.getByRole("button", { name: "+ Добавить поле" }));
  fireEvent.change(screen.getByLabelText("Название поля"), {
    target: { value: "Основание" },
  });
  fireEvent.change(screen.getByLabelText(/^Системный ключ/), {
    target: { value: "reason" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Опубликовать" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const put = fetch.mock.calls.find(
    ([url]) => url === "/api/v1/form-schemas/schema",
  );
  expect(JSON.parse(put![1].body).schema.sections[0].fields[1]).toMatchObject({
    key: "reason",
    label: "Основание",
    type: "textarea",
  });
  const publish = fetch.mock.calls.find(([url]) => url.endsWith("/publish"));
  expect(publish![1].body).toBe("{}");
  expect(publish![1].headers.get("X-CSRF-Token")).toBe("csrf");
});
it("preview uses renderer and published snapshots cannot be mutated", async () => {
  setup({ ...version, state: "published" });
  const add = (await screen.findByRole("button", {
    name: "+ Добавить поле",
  })) as HTMLButtonElement;
  expect(add.closest("fieldset")!.disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "Опубликовать" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Как читатель" }));
  expect(screen.getByText("Предпросмотр · читатель")).toBeTruthy();
  expect(screen.getByText("Не указано")).toBeTruthy();
});
