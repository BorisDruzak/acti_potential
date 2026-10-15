import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DynamicForm } from "./DynamicForm";
import { conditionMatches, editableValues, type Field, type FormSchema } from "./schema";

afterEach(cleanup);
const amount: Field = { key: "amount", label: "Сумма", type: "decimal" };
const dependent: Field = {
  key: "detail", label: "Пояснение", type: "text",
  visible_when: { field: "amount", op: "eq", value: 1.5 },
};
const schema: FormSchema = { sections: [{ key: "main", title: "", fields: [amount, dependent] }] };

it("keeps decimal conditions equal after persisted string normalization", () => {
  render(<DynamicForm schema={schema} values={{ amount: "1.50", detail: "Видимый текст" }} mode="edit" roles={["editor"]} />);
  expect(screen.getByLabelText("Пояснение")).toBeTruthy();
  expect(editableValues(schema, { amount: "1.50", detail: "Сохранить" }, "edit", ["editor"]))
    .toEqual({ amount: "1.50", detail: "Сохранить" });
});

it("compares decimal predicates exactly without coercing text or boolean values", () => {
  const fields = [amount];
  const matches = (value: unknown, expected: unknown) => conditionMatches(
    { field: "amount", op: "eq", value: expected }, { amount: value }, fields,
  );
  expect(matches("1234567890.123456789", "1234567890.123456788")).toBe(false);
  expect(matches("1.00", 1)).toBe(true);
  expect(matches("1e2", "100.00")).toBe(true);
  expect(matches("0.00", false)).toBe(false);
  expect(conditionMatches({ field: "text", op: "eq", value: 1 }, { text: "1" }, [{ key: "text", label: "Текст", type: "text" }])).toBe(false);
});

it("preserves editor display mode in readonly groups and their root conditions", () => {
  const grouped: FormSchema = { sections: [{ key: "main", title: "", fields: [amount, {
    key: "rows", label: "Строки", type: "group", editable: false,
    children: [{ ...dependent, modes: ["editor"] }, { key: "viewer", label: "Для читателя", type: "text", modes: ["viewer"] }],
  }] }] };
  render(<DynamicForm schema={grouped} values={{ amount: "1.50", rows: [{ detail: "Видно редактору", viewer: "Не в этом режиме" }] }} mode="edit" roles={["editor"]} />);
  expect(screen.getByText("Видно редактору")).toBeTruthy();
  expect(screen.queryByText("Не в этом режиме")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Пояснение" })).toBeNull();
});
