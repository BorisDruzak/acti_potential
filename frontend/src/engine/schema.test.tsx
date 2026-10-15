import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DynamicForm } from "./DynamicForm";
import {
  conditionMatches,
  editableValues,
  initialValues,
  prunePendingFiles,
  type FormSchema,
} from "./schema";

afterEach(cleanup);
const schema: FormSchema = {
  sections: [
    {
      key: "main",
      title: "Основное",
      fields: [
        {
          key: "title",
          label: "Заголовок",
          type: "text",
          required: true,
          default: "Новый акт",
        },
        { key: "publish", label: "Публиковать", type: "boolean" },
        {
          key: "reason",
          label: "Основание",
          type: "textarea",
          visible_when: { field: "publish", op: "eq", value: true },
        },
        {
          key: "secret",
          label: "Закрытое поле",
          type: "text",
          read_roles: ["admin"],
        },
        {
          key: "people",
          label: "Участники",
          type: "group",
          children: [{ key: "name", label: "Имя", type: "text" }],
        },
      ],
    },
  ],
};
it("initializes defaults without flattening groups", () => {
  expect(initialValues(schema)).toEqual({ title: "Новый акт" });
});
it("evaluates only declarative condition operators", () => {
  expect(
    conditionMatches({ field: "n", op: "in", value: [1, 2] }, { n: 2 }),
  ).toBe(true);
  expect(conditionMatches({ field: "n", op: "present" }, { n: false })).toBe(
    true,
  );
  expect(
    conditionMatches({ field: "n", op: "unknown" as "eq", value: 1 }, { n: 1 }),
  ).toBe(false);
});
it("renders role, mode and conditional restrictions and a nullable boolean", () => {
  render(
    <DynamicForm
      schema={schema}
      values={{}}
      onChange={vi.fn()}
      mode="edit"
      roles={["editor"]}
    />,
  );
  expect(screen.queryByLabelText("Закрытое поле")).toBeNull();
  expect(screen.queryByLabelText("Основание")).toBeNull();
  const boolean = screen.getByLabelText("Публиковать") as HTMLSelectElement;
  expect(boolean.value).toBe("");
  expect(Array.from(boolean.options).map((option) => option.text)).toEqual([
    "Не указано",
    "Да",
    "Нет",
  ]);
});
it("reader form contains no mutation controls, including repeatable groups", () => {
  render(
    <DynamicForm
      schema={schema}
      values={{ people: [{ name: "Пример" }] }}
      mode="view"
      roles={["viewer"]}
    />,
  );
  expect(screen.getByText("Пример")).toBeTruthy();
  expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  expect(screen.queryAllByRole("button")).toHaveLength(0);
});
it("repeatable groups add independently editable rows", () => {
  const change = vi.fn();
  render(
    <DynamicForm
      schema={schema}
      values={{}}
      onChange={change}
      mode="create"
      roles={["editor"]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Добавить: Участники" }));
  expect(change).toHaveBeenCalledWith({ people: [{}] });
});
it("omits all hidden and read-only values from mutation payloads", () => {
  const guarded: FormSchema = {
    sections: [
      {
        key: "main",
        title: "",
        fields: [
          { key: "title", label: "Название", type: "text" },
          { key: "locked", label: "Закрыто", type: "text", editable: false },
          {
            key: "secret",
            label: "Секрет",
            type: "text",
            write_roles: ["admin"],
          },
          {
            key: "rows",
            label: "Строки",
            type: "group",
            children: [
              { key: "note", label: "Заметка", type: "text" },
              {
                key: "hidden",
                label: "Скрыто",
                type: "text",
                read_roles: ["admin"],
              },
            ],
          },
        ],
      },
    ],
  };
  expect(
    editableValues(
      guarded,
      {
        title: "Акт",
        locked: "Старое",
        secret: "Тайна",
        rows: [{ note: "Открыто", hidden: "Секрет" }],
      },
      "edit",
      ["editor"],
    ),
  ).toEqual({ title: "Акт", rows: [{ note: "Открыто" }] });
});
it("uses top-level conditions for repeatable children and mode-specific fields", () => {
  const nested: FormSchema = {
    sections: [
      {
        key: "main",
        title: "",
        fields: [
          { key: "show", label: "Показывать", type: "boolean" },
          {
            key: "rows",
            label: "Строки",
            type: "group",
            children: [
              {
                key: "note",
                label: "Заметка",
                type: "text",
                visible_when: { field: "show", op: "eq", value: true },
              },
            ],
          },
        ],
      },
    ],
  };
  render(
    <DynamicForm
      schema={nested}
      values={{ show: true, rows: [{ note: "Заполнено" }] }}
      mode="edit"
      roles={["editor"]}
      onChange={vi.fn()}
    />,
  );
  expect((screen.getByLabelText("Заметка") as HTMLInputElement).value).toBe(
    "Заполнено",
  );
  expect(
    editableValues(
      nested,
      { show: false, rows: [{ note: "Скрыто" }] },
      "edit",
      ["editor"],
    ),
  ).toEqual({ show: false, rows: [{}] });
});
it("legacy user role retains editor field access and decimal precision", () => {
  const change = vi.fn();
  render(
    <DynamicForm
      schema={{
        sections: [
          {
            key: "main",
            title: "",
            fields: [
              {
                key: "amount",
                label: "Сумма",
                type: "decimal",
                read_roles: ["editor"],
                write_roles: ["editor"],
              },
            ],
          },
        ],
      }}
      values={{}}
      onChange={change}
      mode="edit"
      roles={["user"]}
    />,
  );
  fireEvent.change(screen.getByLabelText("Сумма"), {
    target: { value: "1234567890.123456789" },
  });
  expect(change).toHaveBeenCalledWith({ amount: "1234567890.123456789" });
});
it("drops queued attachments hidden by conditions and clears shifted group paths after row deletion", () => {
  const guarded: FormSchema = {
    sections: [
      {
        key: "main",
        title: "",
        fields: [
          { key: "gate", label: "Условие", type: "boolean" },
          {
            key: "original",
            label: "Файл",
            type: "file",
            visible_when: { field: "gate", op: "eq", value: true },
          },
          {
            key: "rows",
            label: "Строки",
            type: "group",
            children: [{ key: "file", label: "Вложение", type: "file" }],
          },
        ],
      },
    ],
  };
  expect(
    prunePendingFiles(
      { original: "one", "rows.0.file": "two", "rows.1.file": "three" },
      guarded,
      { gate: false, rows: [{}] },
      ["editor"],
      "create",
      { gate: true, rows: [{}, {}] },
    ),
  ).toEqual({});
  expect(
    prunePendingFiles(
      { original: "one", "rows.0.file": "two" },
      guarded,
      { gate: true, rows: [{}] },
      ["editor"],
      "edit",
    ),
  ).toEqual({ original: "one", "rows.0.file": "two" });
});
