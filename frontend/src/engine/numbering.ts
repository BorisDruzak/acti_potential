export type NumberSeries = {
  id: string;
  code: string;
  name: string;
  description: string;
  registry_id: string;
  document_type_ids: string[];
  organization_scope: string;
  period_mode: "annual" | "manual";
  timezone: string;
  boundary_month: number;
  boundary_day: number;
  current_period: string | null;
  start: number;
  step: number;
  prefix: string;
  suffix: string;
  padding: number;
  format: string;
  state: "active" | "blocked";
  max_batch: number;
  reservation_hours: number | null;
  max_reservation_hours: number;
  allow_indefinite: boolean;
  synthetic: boolean;
  permissions: string[];
};
export type NumberEntry = {
  id: string;
  series_id: string;
  period_key: string;
  ordinal: number;
  number: string;
  state: string;
  reserved_at: string;
  expires_at: string | null;
  document_id: string | null;
  document_title: string | null;
  owner_id?: string | null;
};
export const numberStates: Record<string, string> = {
  reserved: "Зарезервирован", assigned: "Присвоен", cancelled: "Отменён",
  expired: "Срок истёк", void: "Погашен",
};
export const numberPermissions: [string, string][] = [
  ["view", "Просмотр"], ["reserve", "Резервирование"], ["assign", "Присвоение"],
  ["cancel", "Отмена резерва"], ["void", "Погашение"],
];
