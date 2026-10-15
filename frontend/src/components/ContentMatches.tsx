import type { ReactNode } from "react";
import type { ContentMatch, ContentTarget } from "../engine/content";

export function HighlightedSnippet({text, query}: {text: string; query: string}) {
  const terms = Array.from(new Set(query.toLocaleLowerCase("ru-RU").split(/\s+/).filter(term => term.length > 1))).slice(0, 12);
  const lower = text.toLocaleLowerCase("ru-RU");
  const result: ReactNode[] = [];
  let position = 0;
  while (position < text.length) {
    let next = -1;
    let length = 0;
    for (const term of terms) {
      const index = lower.indexOf(term, position);
      if (index >= 0 && (next < 0 || index < next || (index === next && term.length > length))) {
        next = index; length = term.length;
      }
    }
    if (next < 0) {result.push(text.slice(position)); break;}
    if (next > position) result.push(text.slice(position, next));
    result.push(<mark key={next}>{text.slice(next, next + length)}</mark>);
    position = next + length;
  }
  return <>{result}</>;
}

export function ContentMatches({matches, query, onOpen}: {
  matches: ContentMatch[]; query: string; onOpen: (target: ContentTarget) => void;
}) {
  return <div className="content-matches">{matches.map((match, index) => <article key={`${match.block_id || match.field_key || "match"}-${index}`}>
    <p className="match-source">{match.source === "file" ? `${match.label || "Оригинал"} · версия ${match.version || 1}` : `Реквизиты · ${match.label || match.field_key || "Карточка"}`}</p>
    <p className="content-text"><HighlightedSnippet text={match.snippet} query={query}/></p>
    {match.source === "file" && <button type="button" className="text-button" onClick={() => onOpen({fileId: match.file_id, blockId: match.block_id, ordinal: match.block_ordinal})}>Открыть фрагмент источника</button>}
  </article>)}</div>;
}
