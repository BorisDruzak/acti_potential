import { useState, type FormEvent } from "react";
import { Feedback } from "../components/Feedback";
import { SchemaBuilder } from "../engine/SchemaBuilder";
import {
  type DocumentType,
  type ReferenceSet,
  type Registry,
  type SchemaVersion,
} from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { ReferenceItems } from "./ReferenceItems";

function TypeSettings({
  type,
  onSaved,
}: {
  type: DocumentType;
  onSaved: () => void;
}) {
  const [name, setName] = useState(type.name);
  const [description, setDescription] = useState(type.description || "");
  const action = useAction();
  return (
    <details className="panel">
      <summary>Название, описание и назначение реестрам</summary>
      <form
        className="compact-form"
        onSubmit={(event) => {
          event.preventDefault();
          void action.run(async () => {
            await action.request("/document-types/" + type.id, "PATCH", {
              name,
              description,
            });
            onSaved();
          });
        }}
      >
        <label>
          Название типа
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          Описание типа
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <button disabled={action.busy}>Сохранить описание типа</button>
        <Feedback {...action} />
      </form>
      <TypeAssignments typeId={type.id} />
    </details>
  );
}
function TypeAssignments({ typeId }: { typeId: string }) {
  const registries = useData<Registry[]>("/registries");
  const [selection, setSelection] = useState<Record<string, boolean>>({});
  const action = useAction();
  return (
    <form
      className="compact-form"
      onSubmit={(event) => {
        event.preventDefault();
        void action.run(async () => {
          for (const registry of registries.data || []) {
            const assigned =
              selection[registry.id] ??
              registry.document_type_ids.includes(typeId);
            if (assigned !== registry.document_type_ids.includes(typeId))
              await action.request("/registries/" + registry.id, "PATCH", {
                document_type_ids: assigned
                  ? [...registry.document_type_ids, typeId]
                  : registry.document_type_ids.filter((id) => id !== typeId),
              });
          }
          setSelection({});
          registries.reload();
        }, "Назначения реестрам сохранены");
      }}
    >
      <fieldset>
        <legend>Реестры для этого типа</legend>
        <div className="check-list">
          {registries.data?.map((registry) => (
            <label key={registry.id}>
              <input
                type="checkbox"
                checked={
                  selection[registry.id] ??
                  registry.document_type_ids.includes(typeId)
                }
                onChange={(event) =>
                  setSelection((current) => ({
                    ...current,
                    [registry.id]: event.target.checked,
                  }))
                }
              />
              {registry.name}
            </label>
          ))}
        </div>
      </fieldset>
      <Feedback
        error={registries.error || action.error}
        message={action.message}
      />
      <button disabled={action.busy || registries.loading}>
        Сохранить назначения
      </button>
    </form>
  );
}

export function TypesAdmin() {
  const types = useData<DocumentType[]>("/document-types");
  const references = useData<ReferenceSet[]>("/references");
  const [selected, setSelected] = useState("");
  const [versionId, setVersionId] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const versions = useData<SchemaVersion[]>(
    selected ? "/document-types/" + selected + "/versions" : null,
  );
  const version =
    versions.data?.find((item) => item.id === versionId) ||
    (versions.data?.length
      ? versions.data.reduce((latest, item) =>
          item.version > latest.version ? item : latest,
        )
      : undefined);
  const action = useAction();
  async function create(event: FormEvent) {
    event.preventDefault();
    await action.run(async () => {
      const created = await action.request<DocumentType>(
        "/document-types",
        "POST",
        { name, code, description },
      );
      setSelected(created.id);
      setVersionId("");
      setCreating(false);
      types.reload();
    });
  }
  return (
    <>
      <div className="toolbar">
        <h1>Типы документов</h1>
        <button
          className="primary"
          onClick={() => {
            setCreating(true);
            setName("");
            setCode("");
            setDescription("");
          }}
        >
          Создать тип
        </button>
      </div>
      <p>Структура и форма каждого типа настраиваются без разработки.</p>
      <Feedback
        error={
          types.error || versions.error || references.error || action.error
        }
        message={action.message}
        loading={types.loading || versions.loading}
      />
      {creating && (
        <form className="panel compact-form" onSubmit={create}>
          <h2>Новый тип документа</h2>
          <label>
            Название типа
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Системный код
            <input
              required
              pattern="[a-z][a-z0-9_]*"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </label>
          <label>
            Описание типа
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <div className="button-row">
            <button className="primary" disabled={action.busy}>
              Создать
            </button>
            <button type="button" onClick={() => setCreating(false)}>
              Отмена
            </button>
          </div>
        </form>
      )}
      <div className="toolbar type-picker">
        <label>
          Тип документа
          <select
            value={selected}
            onChange={(event) => {
              setSelected(event.target.value);
              setVersionId("");
            }}
          >
            <option value="">Выберите тип</option>
            {types.data?.map((type) => (
              <option key={type.id} value={type.id}>
                {type.name} · {type.code}
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <>
            <label>
              Версия формы
              <select
                value={version?.id || ""}
                onChange={(event) => setVersionId(event.target.value)}
              >
                {versions.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    Версия {item.version} ·{" "}
                    {item.state === "published" ? "Опубликована" : "Черновик"}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  const next = await action.request<SchemaVersion>(
                    "/document-types/" + selected + "/versions",
                    "POST",
                    {},
                  );
                  setVersionId(next.id);
                  versions.reload();
                }, "Новая версия создана")
              }
            >
              Новая версия
            </button>
            <button
              onClick={() => {
                setName(
                  (types.data?.find((type) => type.id === selected)?.name ||
                    "") + " — копия",
                );
                setCode("");
                setDescription("__copy__");
              }}
            >
              Копировать тип
            </button>
          </>
        )}
      </div>
      {description === "__copy__" && !creating && (
        <form
          className="panel compact-form"
          onSubmit={(event) => {
            event.preventDefault();
            void action.run(async () => {
              const copy = await action.request<DocumentType>(
                "/document-types/" + selected + "/copy",
                "POST",
                { code, name },
              );
              setSelected(copy.id);
              setVersionId("");
              setDescription("");
              types.reload();
            }, "Копия создана");
          }}
        >
          <h3>Копия типа</h3>
          <label>
            Название копии
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Код копии
            <input
              required
              pattern="[a-z][a-z0-9_]*"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </label>
          <div className="button-row">
            <button className="primary" disabled={action.busy}>
              Создать копию
            </button>
            <button type="button" onClick={() => setDescription("")}>
              Отмена
            </button>
          </div>
        </form>
      )}
      {types.data?.find((type) => type.id === selected) && (
        <TypeSettings
          key={selected}
          type={types.data.find((type) => type.id === selected)!}
          onSaved={types.reload}
        />
      )}
      {version && (
        <SchemaBuilder
          key={version.id + ":" + version.state}
          version={version}
          references={references.data || []}
          onSaved={versions.reload}
        />
      )}
      {!selected && !types.loading && (
        <div className="empty-state">
          Выберите существующий тип или создайте новый.
        </div>
      )}
    </>
  );
}

function RegistryEditor({
  registry,
  types,
  onSaved,
  onClose,
}: {
  registry: Registry | null;
  types: DocumentType[];
  onSaved: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(registry?.name || "");
  const [code, setCode] = useState(registry?.code || "");
  const [description, setDescription] = useState(registry?.description || "");
  const [ids, setIds] = useState(registry?.document_type_ids || []);
  const action = useAction();
  return (
    <form
      className="panel compact-form"
      onSubmit={(event) => {
        event.preventDefault();
        void action.run(async () => {
          await action.request(
            "/registries" + (registry ? "/" + registry.id : ""),
            registry ? "PATCH" : "POST",
            { name, code, description, document_type_ids: ids },
          );
          onSaved();
          onClose();
        });
      }}
    >
      <h2>{registry ? "Настройки реестра" : "Создать реестр"}</h2>
      <label>
        Название реестра
        <input
          value={name}
          required
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        Код реестра
        <input
          value={code}
          required
          onChange={(event) => setCode(event.target.value)}
        />
      </label>
      <label>
        Описание
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <fieldset>
        <legend>Доступные типы документов</legend>
        <div className="check-list">
          {types.map((type) => (
            <label key={type.id}>
              <input
                type="checkbox"
                checked={ids.includes(type.id)}
                onChange={(event) =>
                  setIds((current) =>
                    event.target.checked
                      ? [...current, type.id]
                      : current.filter((id) => id !== type.id),
                  )
                }
              />
              {type.name}
            </label>
          ))}
        </div>
      </fieldset>
      <Feedback {...action} />
      <div className="button-row">
        <button className="primary" disabled={action.busy}>
          Сохранить реестр
        </button>
        <button type="button" onClick={onClose}>
          Отмена
        </button>
      </div>
    </form>
  );
}
export function RegistriesAdmin() {
  const registries = useData<Registry[]>("/registries");
  const types = useData<DocumentType[]>("/document-types");
  const [editing, setEditing] = useState<Registry | null | undefined>(
    undefined,
  );
  return (
    <>
      <div className="toolbar">
        <h1>Реестры</h1>
        <button className="primary" onClick={() => setEditing(null)}>
          Создать реестр
        </button>
      </div>
      <p>
        Назначьте типы документов реестрам. Права на содержимое выдаются
        отдельно.
      </p>
      <Feedback
        error={registries.error || types.error}
        loading={registries.loading}
      />
      {editing !== undefined && (
        <RegistryEditor
          key={editing?.id || "new"}
          registry={editing}
          types={types.data || []}
          onSaved={registries.reload}
          onClose={() => setEditing(undefined)}
        />
      )}
      <div className="module-grid">
        {registries.data?.map((registry) => (
          <button
            className="module-card"
            key={registry.id}
            onClick={() => setEditing(registry)}
          >
            <span className="badge">{registry.code}</span>
            <h3>{registry.name}</h3>
            <p>{registry.description || "Настройки и типы документов"}</p>
            <small>{registry.document_type_ids.length} типов</small>
          </button>
        ))}
      </div>
    </>
  );
}

export function ReferencesAdmin() {
  const references = useData<ReferenceSet[]>("/references");
  const registries = useData<Registry[]>("/registries");
  const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [registryId, setRegistryId] = useState("");
  const action = useAction();
  return (
    <>
      <div className="toolbar">
        <h1>Справочники</h1>
        <button className="primary" onClick={() => setCreating(true)}>
          Создать справочник
        </button>
      </div>
      <p>Общие и отдельные для каждого реестра наборы значений.</p>
      <Feedback
        error={references.error || registries.error || action.error}
        loading={references.loading}
      />
      {creating && (
        <form
          className="panel compact-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!registryId) return;
            void action.run(async () => {
              const created = await action.request<ReferenceSet>(
                "/references",
                "POST",
                { name, code, registry_id: registryId === "global" ? null : registryId },
              );
              setSelected(created.id);
              setCreating(false);
              references.reload();
            });
          }}
        >
          <label>
            Название справочника
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Код справочника
            <input
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </label>
          <label>
            Область справочника
            <select
              required
              value={registryId}
              onChange={(event) => setRegistryId(event.target.value)}
            >
              <option value="" disabled>Выберите область</option>
              <option value="global">Общий для всех реестров</option>
              {registries.data?.map((registry) => (
                <option key={registry.id} value={registry.id}>
                  {registry.name}
                </option>
              ))}
            </select>
          </label>
          <div className="button-row">
            <button className="primary" disabled={action.busy}>
              Создать справочник
            </button>
            <button type="button" onClick={() => setCreating(false)}>
              Отмена
            </button>
          </div>
        </form>
      )}
      <label className="picker">
        Справочник
        <select
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">Выберите справочник</option>
          {references.data?.map((reference) => (
            <option key={reference.id} value={reference.id}>
              {reference.name} ·{" "}
              {registries.data?.find(
                (registry) => registry.id === reference.registry_id,
              )?.name || "общий"}
            </option>
          ))}
        </select>
      </label>
      {references.data?.find((reference) => reference.id === selected) && (
        <ReferenceItems
          key={selected}
          reference={references.data.find(
            (reference) => reference.id === selected,
          )!}
        />
      )}
    </>
  );
}
