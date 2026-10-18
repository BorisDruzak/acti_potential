// Form controls show missing and cleared optional values in the same way.
export function sameFormValue(left: unknown, right: unknown): boolean {
  const empty = (value: unknown) => value === undefined || value === null || value === "" || Array.isArray(value) && value.length === 0;
  if (empty(left) || empty(right)) return empty(left) && empty(right);
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => sameFormValue(value, right[index]));
  }
  if (typeof left === "object" && typeof right === "object") {
    const a = left as Record<string, unknown>, b = right as Record<string, unknown>;
    return [...new Set([...Object.keys(a), ...Object.keys(b)])].every(key => sameFormValue(a[key], b[key]));
  }
  return Object.is(left, right);
}
