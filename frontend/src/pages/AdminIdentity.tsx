import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { type Registry } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
type User = {
  id: string;
  login: string;
  display_name: string;
  active: boolean;
  roles: string[];
  must_change_password: boolean;
};
const roleNames: Record<string, string> = {
  admin: "Администратор",
  editor: "Редактор",
  viewer: "Читатель",
  user: "Пользователь",
};
const actions: Record<string, string> = {
  read: "Просмотр карточек и истории",
  create: "Создание",
  update: "Изменение",
  upload: "Загрузка файлов",
  download: "Скачивание файлов",
  manage: "Управление документами",
};
function RolePicker({
  roles,
  onChange,
}: {
  roles: string[];
  onChange: (roles: string[]) => void;
}) {
  return (
    <fieldset>
      <legend>Роли</legend>
      <div className="check-list">
        {Object.entries(roleNames).map(([key, label]) => (
          <label key={key}>
            <input
              type="checkbox"
              checked={roles.includes(key)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...roles, key]
                    : roles.filter((role) => role !== key),
                )
              }
            />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
function UserEditor({
  user,
  onSaved,
  onClose,
}: {
  user: User | null;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [login, setLogin] = useState(user?.login || "");
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [roles, setRoles] = useState(user?.roles || ["viewer"]);
  const [active, setActive] = useState(user?.active ?? true);
  const [password, setPassword] = useState("");
  const action = useAction();
  return (
    <section className="panel compact-form">
      <h2>{user ? "Учётная запись" : "Новый пользователь"}</h2>
      <form
        className="compact-form"
        onSubmit={(event) => {
          event.preventDefault();
          void action.run(async () => {
            await action.request(
              "/admin/users" + (user ? "/" + user.id : ""),
              user ? "PATCH" : "POST",
              user
                ? { display_name: displayName, roles, active }
                : { login, display_name: displayName, password, roles },
            );
            onSaved();
            onClose();
          });
        }}
      >
        <label>
          Логин
          <input
            disabled={!!user}
            required
            value={login}
            autoComplete="off"
            onChange={(event) => setLogin(event.target.value)}
          />
        </label>
        <label>
          Отображаемое имя
          <input
            required
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </label>
        <RolePicker roles={roles} onChange={setRoles} />
        {user ? (
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={active}
              onChange={(event) => setActive(event.target.checked)}
            />
            Учётная запись активна
          </label>
        ) : (
          <label>
            Временный пароль
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={14}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <small>
              Передайте пользователю по защищённому каналу. При первом входе
              потребуется сменить пароль.
            </small>
          </label>
        )}
        <div className="button-row">
          <button className="primary" disabled={action.busy}>
            Сохранить пользователя
          </button>
          <button type="button" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
      {user && (
        <form
          className="compact-form"
          onSubmit={(event) => {
            event.preventDefault();
            void action.run(async () => {
              await action.request(
                "/admin/users/" + user.id + "/password",
                "POST",
                { password },
              );
              setPassword("");
              onSaved();
            }, "Временный пароль установлен. Действующие сессии отозваны.");
          }}
        >
          <h3>Сброс пароля</h3>
          <label>
            Новый временный пароль
            <input
              type="password"
              required
              minLength={14}
              value={password}
              autoComplete="new-password"
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <button disabled={action.busy}>Установить временный пароль</button>
        </form>
      )}
      <Feedback {...action} />
    </section>
  );
}
export function UsersAdmin() {
  const users = useData<User[]>("/admin/users");
  const [selected, setSelected] = useState<User | null | undefined>(undefined);
  return (
    <>
      <div className="toolbar">
        <h1>Пользователи</h1>
        <button className="primary" onClick={() => setSelected(null)}>
          Создать пользователя
        </button>
      </div>
      <p>Локальные учётные записи. Права на содержимое назначаются отдельно.</p>
      <Feedback {...users} />
      {selected !== undefined && (
        <UserEditor
          key={selected?.id || "new"}
          user={selected}
          onSaved={users.reload}
          onClose={() => setSelected(undefined)}
        />
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Имя</th>
              <th>Логин</th>
              <th>Роли</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {users.data?.map((user) => (
              <tr key={user.id}>
                <td>{user.display_name}</td>
                <td>{user.login}</td>
                <td>
                  {user.roles.map((role) => roleNames[role] || role).join(", ")}
                </td>
                <td>
                  {!user.active
                    ? "Заблокирован"
                    : user.must_change_password
                      ? "Ожидает смены пароля"
                      : "Активен"}
                </td>
                <td>
                  <button onClick={() => setSelected(user)}>Настроить</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
type Grant = { user_id: string; permissions: string[] };
function AccessEditor({
  registryId,
  users,
  documentId,
}: {
  registryId: string;
  users: User[];
  documentId?: string;
}) {
  const path = documentId
    ? "/documents/" + documentId + "/acl"
    : "/admin/access/" + registryId;
  const grants = useData<{ entries: Grant[] } | Grant[]>(path);
  const action = useAction();
  const [entries, setEntries] = useState<Grant[] | null>(null);
  const current =
    entries ||
    (Array.isArray(grants.data) ? grants.data : grants.data?.entries) ||
    [];
  function toggle(userId: string, permission: string, checked: boolean) {
    const existing =
      current.find((entry) => entry.user_id === userId)?.permissions || [];
    const permissions = checked
      ? [...existing, permission]
      : existing.filter((item) => item !== permission);
    setEntries([
      ...current.filter((entry) => entry.user_id !== userId),
      ...(permissions.length ? [{ user_id: userId, permissions }] : []),
    ]);
  }
  return (
    <section className="panel">
      <h2>
        {documentId ? "Индивидуальный доступ к документу" : "Права на реестр"}
      </h2>
      <p>
        {documentId
          ? "Непустой список ограничивает права реестра. Пустой список восстанавливает права реестра."
          : "Администрирование само по себе не открывает документы."}
      </p>
      <Feedback
        error={grants.error || action.error}
        message={action.message}
        loading={grants.loading}
      />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Пользователь</th>
              {Object.entries(actions).map(([key, label]) => (
                <th key={key}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>{user.display_name}</td>
                {Object.keys(actions).map((permission) => (
                  <td key={permission}>
                    <input
                      type="checkbox"
                      aria-label={
                        user.display_name + ": " + actions[permission]
                      }
                      checked={
                        current
                          .find((entry) => entry.user_id === user.id)
                          ?.permissions.includes(permission) || false
                      }
                      onChange={(event) =>
                        toggle(user.id, permission, event.target.checked)
                      }
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="button-row">
        <button
          className="primary"
          disabled={action.busy || grants.loading}
          onClick={() =>
            void action.run(async () => {
              await action.request(path, "PUT", { entries: current });
              setEntries(null);
              grants.reload();
            })
          }
        >
          Сохранить права
        </button>
        {documentId && (
          <button onClick={() => setEntries([])}>
            Очистить ограничения документа
          </button>
        )}
      </div>
    </section>
  );
}
export function AccessAdmin() {
  const registries = useData<Registry[]>("/registries");
  const users = useData<User[]>("/admin/users");
  const [registryId, setRegistryId] = useState("");
  const [documentInput, setDocumentInput] = useState("");
  const [documentId, setDocumentId] = useState("");
  return (
    <>
      <h1>Роли и доступ</h1>
      <p>
        Роли открывают рабочие пространства, разрешения — конкретное содержимое.
      </p>
      <Feedback error={registries.error || users.error} />
      <label className="picker">
        Реестр
        <select
          value={registryId}
          onChange={(event) => setRegistryId(event.target.value)}
        >
          <option value="">Выберите реестр</option>
          {registries.data?.map((registry) => (
            <option key={registry.id} value={registry.id}>
              {registry.name}
            </option>
          ))}
        </select>
      </label>
      {registryId && (
        <AccessEditor
          key={registryId}
          registryId={registryId}
          users={users.data || []}
        />
      )}
      <form
        className="panel inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          setDocumentId(documentInput);
        }}
      >
        <label>
          Индивидуальные права: UUID документа
          <input
            required
            value={documentInput}
            onChange={(event) => setDocumentInput(event.target.value)}
          />
        </label>
        <button>Открыть права документа</button>
      </form>
      {documentId && (
        <AccessEditor
          key={documentId}
          registryId=""
          documentId={documentId}
          users={users.data || []}
        />
      )}
    </>
  );
}
export function AuditAdmin() {
  const [page, setPage] = useState(1);
  const audit = useData<{
    items: Record<string, unknown>[];
    total: number;
    page_size: number;
  }>("/admin/audit?page=" + page + "&page_size=30");
  return (
    <>
      <h1>История и аудит</h1>
      <p>События изменения конфигурации и работы с документами.</p>
      <Feedback {...audit} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Время</th>
              <th>Событие</th>
              <th>Объект</th>
              <th>Участник</th>
            </tr>
          </thead>
          <tbody>
            {audit.data?.items.map((event, index) => (
              <tr key={String(event.id || index)}>
                <td>{String(event.occurred_at || event.created_at || "")}</td>
                <td>{String(event.action || event.event_type || "")}</td>
                <td>
                  {String(event.object_type || "")}
                </td>
                <td>{String(event.actor_id || "Система")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {audit.data?.items.length === 0 && (
        <p className="empty-state">Событий пока нет.</p>
      )}
      <div className="pagination">
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>
          Назад
        </button>
        <span>Страница {page}</span>
        <button
          disabled={!audit.data || page * 30 >= audit.data.total}
          onClick={() => setPage(page + 1)}
        >
          Вперёд
        </button>
      </div>
    </>
  );
}
export function PlatformStatus() {
  const status = useData<{ version: string; worker: string }>(
    "/admin/platform",
  );
  return (
    <>
      <h1>Состояние платформы</h1>
      <Feedback {...status} />
      {status.data && (
        <section className="panel">
          <dl>
            <div>
              <dt>Версия</dt>
              <dd>{status.data.version}</dd>
            </div>
            <div>
              <dt>Фоновый обработчик</dt>
              <dd>
                {status.data.worker === "available" ? "Работает" : "Недоступен"}
              </dd>
            </div>
            <div>
              <dt>Вход</dt>
              <dd>Локальная учётная запись</dd>
            </div>
          </dl>
        </section>
      )}
    </>
  );
}
