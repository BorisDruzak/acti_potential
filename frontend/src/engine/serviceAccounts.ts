export type ServiceOperation = "documents" | "files" | "content" | "relations" | "processes" | "knowledge" | "numbers" | "execution" | "events";
export const serviceOperationNames: Record<ServiceOperation, string> = {documents: "Карточки и версии документов", files: "Оригиналы файлов", content: "Содержание и блоки", relations: "Связи документов", processes: "Описания процессов", knowledge: "Шаблоны и практика", numbers: "Реестр номеров", execution: "Ход исполнения", events: "События изменений"};
export type ServiceToken = {id: string; prefix: string; created_at?: string; expires_at: string; revoked_at?: string | null; registry_ids?: string[]; operations?: ServiceOperation[]};
export type ServiceAccount = {id: string; user_id: string; name: string; state: "active" | "disabled"; registry_ids: string[]; operations: ServiceOperation[]; created_at: string; tokens: ServiceToken[]};
export type OneTimeToken = {id: string; token: string; prefix: string; expires_at: string};
export type ServiceScopeOptions = {registries: {id: string; name: string; permissions?: string[]}[]; operations: {id: ServiceOperation; label: string}[]};
export type AdminPage<T> = {items: T[]; total: number; page: number; page_size: number};
export function serviceTokenState(token: ServiceToken, currentTime = Date.now()) {return token.revoked_at ? "Отозван" : Date.parse(token.expires_at) <= currentTime ? "Истёк" : "Действует";}
