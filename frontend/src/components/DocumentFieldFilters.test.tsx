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
