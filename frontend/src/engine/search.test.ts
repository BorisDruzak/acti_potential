import { expect, it } from "vitest";
import { searchBody } from "./search";

it("keeps card lifecycle distinct from the legal status filter", () => {
  expect(searchBody("card_state=draft&status=active", "r", "editor")).toMatchObject({card_state: "draft", status: "active"});
});
it("does not send a reader's restored draft filter to search or export", () => {
  expect(searchBody("card_state=draft", "", "viewer")).toMatchObject({card_state: "published", mode: "viewer"});
});
it("preserves a card publication target alongside legal publication field predicates", () => {
  expect(searchBody('publication_target=shared&filters=%7B%22should_publish%22%3Afalse%7D', "", "editor")).toMatchObject({publication_target: "shared", filters: {should_publish: false}});
});
