export type SearchScope = "all" | "metadata" | "content";

export function searchBody(search: string, registryId: string, mode: string, cursor?: string) {
  const params = new URLSearchParams(search);
  let filters = {};
  try { filters = JSON.parse(params.get("filters") || "{}"); } catch { /* Server validates restored values. */ }
  return {
    query: params.get("q") || params.get("text") || "",
    scope: params.get("scope") || (params.has("text") ? "all" : "all"),
    registry_ids: registryId ? [registryId] : [],
    document_type_ids: params.get("document_type_id") ? [params.get("document_type_id")] : [],
    card_state: mode === "viewer" ? "published" : params.get("card_state") || null,
    reg_number: params.get("reg_number") || null, status: params.get("status") || null,
    date_from: params.get("reg_date") || params.get("date_from") || null,
    date_to: params.get("reg_date") || params.get("date_to") || null,
    filters, fuzzy: params.get("fuzzy") !== "false", sort: params.get("sort") || "auto",
    direction: params.get("direction") || "desc", version_scope: params.get("version_scope") || "current",
    page_size: 20, mode, cursor: cursor || null,
  };
}
