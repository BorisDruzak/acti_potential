import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DocumentFieldFilters, parseFieldFilters, serializeFieldFilters } from "./DocumentFieldFilters";
afterEach(cleanup);
it("restores every typed field predicate and removes one without changing the others", () => {
  const predicates = {one: {op: "between", value: [1, 5]}, two: "value", three: {op: "present"}};
  const restored = parseFieldFilters(JSON.stringify(predicates));
  expect(restored).toHaveLength(3);
  expect(serializeFieldFilters(restored)).toEqual(predicates);
  const onChange = vi.fn();
  render(<DocumentFieldFilters filters={restored} fields={[]} references={[]} registryId="" roles={[]} readOnly={false} onChange={onChange}/>);
  fireEvent.click(screen.getByRole("button", {name: "Удалить фильтр 2"}));
  expect(serializeFieldFilters(onChange.mock.calls[0][0])).toEqual({one: predicates.one, three: predicates.three});
});
it("offers explicit yes, no and unspecified boolean values and serializes unspecified as absent", () => {
  const onChange = vi.fn();
  render(<DocumentFieldFilters filters={[{key: "publish", op: "eq", value: false}]} fields={[{key: "publish", label: "Публикация", type: "boolean"}]} references={[]} registryId="" roles={[]} readOnly={false} onChange={onChange}/>);
  const value = screen.getByLabelText("Значение фильтра 1");
  expect(value.tagName).toBe("SELECT");
  expect(screen.getByRole("option", {name: "Да"})).toBeTruthy();
  fireEvent.change(value, {target: {value: "unspecified"}});
  expect(serializeFieldFilters(onChange.mock.calls[0][0])).toEqual({publish: {op: "absent"}});
});
