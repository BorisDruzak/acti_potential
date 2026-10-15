import type { Card } from "./schema";

export type ContentBlock = {
  id: string; kind: string; ordinal: number; parent_id: string | null;
  page: number | null; anchor: string; text: string; table?: string[][] | null;
};
export type ContentExtraction = {
  id: string; file_id: string; version_id: string; version: number; name: string;
  sha256: string; parser: string; parser_version: string; state: string;
  attempts: number; error_code: string | null; warnings: string[];
  started_at: string | null; completed_at: string | null;
  blocks: ContentBlock[]; block_count?: number; truncated?: boolean;
};
export type ContentMatch = {
  source: "metadata" | "file"; field_key?: string; file_id?: string;
  version_id?: string; version?: number; block_id?: string; block_ordinal?: number;
  snippet: string; rank: number; label: string;
};
export type ContentHit = { document: Card; matches: ContentMatch[] };
export type ContentTarget = { fileId?: string; blockId?: string; ordinal?: number };

export const extractionStates: Record<string, string> = {
  pending: "Ожидает обработки", running: "Обрабатывается", completed: "Содержание извлечено",
  no_text: "Нет цифрового текстового слоя", unsupported: "Формат не поддерживается",
  encrypted: "Файл защищён паролем", failed: "Ошибка обработки",
};
export const extractionErrors: Record<string, string> = {
  malformed: "Файл повреждён или имеет неверную структуру.",
  parse_failed: "Не удалось прочитать структуру файла.",
  timeout: "Превышено время обработки файла.",
  too_large: "Файл превышает допустимый размер обработки.",
  encrypted: "Защищённый паролем файл нельзя обработать.",
  unsupported: "Для этого формата пока нет обработчика.",
  no_text: "Цифровой текст отсутствует. Распознавание сканов пока не подключено.",
  malformed_document: "Файл повреждён или имеет неверную структуру.",
  parser_failed: "Не удалось прочитать структуру файла.",
  parser_timeout: "Превышено время обработки файла.",
  limit_exceeded: "Сложность или размер файла превышают допустимые ограничения обработки.",
  storage_unavailable: "Оригинал временно недоступен. Обработка будет повторена.",
  worker_interrupted: "Обработка была прервана. Повторите попытку.",
};
export const extractionWarnings: Record<string, string> = {
  external_relationships_ignored: "Внешние ссылки не загружались при обработке документа.",
  nested_table_omitted: "Вложенная таблица не включена в извлечённое содержание. Проверьте оригинал.",
  legacy_doc_layout_flattened: "Содержание DOC извлечено как текст; оформление доступно в оригинале.",
};
export const blockKinds: Record<string, string> = {
  paragraph: "Абзац", heading: "Заголовок", table: "Таблица", table_row: "Строка таблицы",
  page: "Страница", point: "Пункт", list_item: "Пункт списка", section: "Раздел",
};
