# ACTI v3 Platform Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native implementation. Use superpowers:subagent-driven-development only if the user selects delegation. Steps use checkbox syntax for tracking. The task excludes independent audits and repeated reviews; do not add them through a default skill workflow.

**Goal:** Развернуть на основной ВМ работающую ACTI v3 с локальным входом, защищённым API, PostgreSQL/Alembic и сохранностью данных после пересоздания контейнеров.

**Architecture:** Модульный монолит FastAPI, React/TypeScript, единый origin через Nginx HTTPS. PostgreSQL хранит пользователей, сессии, аудит и outbox; отдельный worker обрабатывает технические события. Compose запускается через проверку обязательных томов.

**Tech Stack:** Python, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16+, Redis, React, TypeScript, Nginx, Docker Compose, pytest, Playwright MCP.

**Spec:** [TASK_001_PLATFORM_FOUNDATION_DESIGN.md](TASK_001_PLATFORM_FOUNDATION_DESIGN.md), согласована пользователем 2026-10-15.

**Status:** План подготовлен для проверки пользователем; реализация не начата.

## Global Constraints

- Работа в `codex/task-001-platform-foundation`; пользовательский diff `AGENTS.md` не включать в коммиты задачи.
- Исходные SQL Server `okrug` и `raion`, их файлы и `legacy/v2/` не изменяются.
- Roadmap не изменяется. Массовый импорт, реестр, AD/OIDC, AI/RAG и следующие задачи не запускаются.
- Стабильный `User.id` не зависит от login. Первичные ключи платформы — UUID; даты операций — UTC `timestamptz`.
- SQLAlchemy 2; PostgreSQL 16+; Alembic вместо `create_all()` при startup.
- Сессия хранится в PostgreSQL, имеет абсолютный срок 12 часов; logout отзывает её на сервере.
- Cookie — HttpOnly, Secure, SameSite=Lax, Path=/, без Domain. Токены в localStorage не используются.
- Данные PostgreSQL, включая WAL: `/srv/acti/postgresql`; версии файлов: `/srv/acti/documents`.
- До записи проверяются реальные mountpoint и UUID из закрытой конфигурации. Автозапуск контейнеров не обходит preflight.
- Секреты, адреса, учётные записи, UUID томов и ключи не включать в Git; текст — UTF-8 без BOM.
- До использования внешних API определить новые pinned versions, разрешить library IDs Context7 и прочитать документацию конкретного API; исходники/локальную конфигурацию туда не отправлять.
- Проверки ORM выполнять на PostgreSQL. Для браузера сначала имеющийся Playwright MCP. Legacy-тесты не являются приёмкой v3.
- Перед каждым коммитом: status, полный diff, diff --check, относящиеся к изменению проверки; stage только файлов задачи. Не делать автоматический push ради GitNexus.
- Один commit на самостоятельный результат ниже; тесно связанные изменения не дробить искусственно.

## Review Focus

1. Две одновременные bootstrap-команды не создают двух первоначальных администраторов: тест в задаче 2.
2. Отключение пользователя/изменение роли влияет на уже выданную сессию: тест в задачах 2–3.
3. Сбой сети/503 на login не выдаётся в UI за неверный пароль: браузерная проверка в задаче 6.
4. Потеря обязательного тома/неверный UUID запрещает запуск и не создаёт fallback-каталог: тест в задачах 1 и 7.
5. Падение worker после обработки до подтверждения не дублирует эффект события: тест в задаче 4.

## Карта файлов и интерфейсов

`backend/app` — Python package; проверки запускаются из `backend/` через `.venv`.
Рабочий checkout на ВМ получает путь из игнорируемой локальной конфигурации,
а не из чужого проекта. Test database — отдельная БД на томе PostgreSQL.
Её имя оканчивается `_test`; destructive test fixtures запрещают другое имя.
Все приведённые команды запускаются из указанного каталога на подготовленной ВМ,
если явно не указано Windows PowerShell. Отчёт записывает реальные команды.

| Область | Основные файлы | Ответственность |
| --- | --- | --- |
| DB/runtime | backend/pyproject.toml, backend/uv.lock, backend/Dockerfile, backend/app/core/config.py, backend/app/db/base.py, backend/app/db/session.py | Воспроизводимые зависимости, config, engine/session |
| Migrations | backend/alembic.ini, backend/migrations/env.py, backend/migrations/versions/0001_platform.py, backend/app/db/migrate.py | Явное создание/обновление схемы, блокировка мигратора |
| Identity | backend/app/identity/models.py, credentials.py, sessions.py, service.py, bootstrap.py, policy.py | Внутренний actor, локальный вход, права |
| HTTP | backend/app/main.py, backend/app/api/{errors,middleware,dependencies,health,auth,users,admin}.py | HTTP-контракты, auth по умолчанию, request ID |
| Platform | backend/app/platform/{models,audit,outbox}.py, backend/app/jobs/worker.py | Транзакционный аудит, события и receipts |
| Storage/import | backend/app/storage/{base,local}.py, migration/import-contract.schema.json, migration/README.md | Провайдер файлов и контракт будущего импорта |
| Frontend | frontend/package.json, package-lock.json, tsconfig.json, vite.config.ts, src/{main,App}.tsx, src/api/client.ts, src/auth/AuthProvider.tsx, src/pages/{Login,Workspace}.tsx, src/components/{Layout,SectionNotice}.tsx, src/styles.css | API client, состояния входа, оболочка и разделы |
| Deployment | deploy/compose.yaml, deploy/.env.example, deploy/preflight.py, deploy/stack.sh, deploy/acti-v3.service, deploy/nginx.conf, deploy/frontend.Dockerfile, deploy/backup.sh, deploy/restore-test.sh | Изоляция портов, mount guard, HTTPS, запуск и восстановление |
| Acceptance | docs/DEPLOYMENT.md, docs/architecture/TASK_001_ACCEPTANCE.md, README.md, project.toml | Команды эксплуатации, фактические результаты и статус |

Файлы с несколькими именами в фигурных скобках в таблице — отдельные файлы.
Ниже перечислены дополнительные test/config файлы каждого результата.

## Task 1: PostgreSQL, миграции и проверяемая среда

**Files:** DB/runtime и Migrations из карты; `backend/app/identity/models.py`, `backend/app/platform/models.py` (ORM declarations); `deploy/compose.yaml`, `deploy/.env.example`, `deploy/preflight.py`; `backend/tests/conftest.py`, `backend/tests/test_migrations.py`, `backend/tests/test_preflight.py`; `.gitignore`.

**Interfaces:** `Settings` читает DSN, Redis URL, cookie/origin settings и storage root без вывода секретов. `build_engine(settings: Settings) -> Engine`; `session_scope(factory: sessionmaker) -> Iterator[Session]` управляет commit/rollback. `upgrade_database(engine: Engine) -> None` берёт PostgreSQL advisory lock на время upgrade. `verify_mounts(expected: Mapping[str, str]) -> None` запрещает несовпадения до startup через `MountVerificationError` из `deploy/preflight.py`.

- [ ] 1. Определить новые совместимые версии и зафиксировать backend lockfile/image versions; проверить нужные API SQLAlchemy/Alembic/settings в Context7. Установить Docker/Compose на ВМ по доступным пакетам Ubuntu без reboot; проверить `docker version` и `docker compose version`. Сопоставить UID/GID выбранного PostgreSQL image и проверить UUID до создания выделенных data/test-подкаталогов.
- [ ] 2. Написать `test_clean_upgrade`, `test_repeat_upgrade`, `test_concurrent_migrators`, `test_wrong_uuid_blocks_start`, `test_missing_mount_has_no_fallback`, `test_fixture_refuses_non_test_database`. Проверяемые утверждения: `current_revision == '0001_platform'`; повторный upgrade не меняет строки; неверный UUID выбрасывает `MountVerificationError`; test fixture отвергает рабочую БД.
- [ ] 3. Запустить `uv run pytest tests/test_migrations.py tests/test_preflight.py -q`; получить ожидаемый RED из-за отсутствующей реализации, а не недоступной test database. Использовать PostgreSQL на подтверждённом томе, не SQLite.
- [ ] 4. Реализовать config/engine/preflight и первоначальные SQLAlchemy models всех согласованных таблиц: identity, audit, outbox, processed receipts. Создать Alembic revision с FK, unique constraints, индексами, ролями admin/user. Добавить безопасный DSN guard test fixture. Обеспечить serial migration runner и временный Compose-контур PostgreSQL/Redis для проверок.
- [ ] 5. Повторить тесты; выполнить `uv run alembic upgrade head`, повторный upgrade и `uv run alembic check`. Ожидается PASS, одна revision и отсутствие diff схемы. Выполнить `uv run ruff check app tests`.
- [ ] 6. Проверить полный diff/секреты и сделать `feat(db): add PostgreSQL platform schema and migrations`. Включить только перечисленные task-файлы, зафиксировать полный SHA.

## Task 2: Локальная идентификация, сессии и роли

**Files:** Identity из карты; `backend/tests/test_identity.py`, `backend/tests/test_bootstrap.py`.

**Consumes:** Task 1 `Settings`, engine, session factory и ORM models.
**Produces:** `Actor(id: UUID, display_name: str, roles: frozenset[str])`; `bootstrap_admin(session: Session, login: str, display_name: str, password: str) -> UUID`; `authenticate_local(session: Session, login: str, password: str) -> User`; `issue_session(session: Session, user_id: UUID, now: datetime) -> IssuedSession`; `resolve_actor(session: Session, token: str, now: datetime) -> Actor`; `revoke_session(session: Session, token: str) -> None`; `require_role(actor: Actor, role: str) -> None`. `IssuedSession` содержит `token`, `csrf_token`, `expires_at`; вызывающая операция управляет транзакцией.

- [ ] 1. Написать тесты: Argon2id hash отличается от password; wrong credentials/disabled user дают одну ошибку; `expires_at - now == timedelta(hours=12)`; БД не содержит исходный token; revoked/expired session отвергается; изменение роли/активности видно существующей сессии; concurrent bootstrap даёт ровно одного admin.
- [ ] 2. Запустить `uv run pytest tests/test_identity.py tests/test_bootstrap.py -q`; зафиксировать ожидаемый RED на отсутствующих функциях.
- [ ] 3. Реализовать signatures выше: нормализация login, Argon2id, случайные tokens, hash lookup, session-bound CSRF, проверки срока/активности/роли. Bootstrap сериализовать в БД; CLI вводит/подтверждает пароль через getpass и не принимает password argv.
- [ ] 4. Запустить тесты до PASS. Дополнительно проверить вывод CLI на повторном bootstrap: отказ без изменения ID/hash и без секрета в output; `ruff check`.
- [ ] 5. Проверить diff/staging и commit `feat(auth): add local identity and persistent sessions`.

## Task 3: Защищённый HTTP API

**Files:** HTTP из карты; `backend/tests/test_auth_api.py`, `test_health_api.py`, `test_errors.py`, `test_route_security.py`.

**Consumes:** Tasks 1–2 settings/session factory/identity functions.
**Produces:** `create_app(settings: Settings) -> FastAPI`; `current_actor(request: Request, session: Session) -> Actor`; `current_admin(actor: Actor) -> Actor`; все HTTP routes из spec §5. Общий protected-router получает обязательную auth dependency; публичные исключения перечислены явно.

- [ ] 1. Написать RED tests для login/me/logout, 401 anonymous, 403 user на admin route и 200 admin, bad Origin/CSRF, cookie flags, rate limit и его fail-closed поведения, live при недоступной БД, ready 503 на DB/Redis/storage/schema mismatch. Ошибки validation/HTTP/500 имеют `error.code`, `error.message`, `error.details`, `request_id`; `X-Request-ID` совпадает с body/log correlation.
- [ ] 2. Запустить `uv run pytest tests/test_auth_api.py tests/test_health_api.py tests/test_errors.py tests/test_route_security.py -q`; проверить RED по реализации. Test route, добавленный в protected-router без отдельной auth annotation, должен возвращать 401.
- [ ] 3. Реализовать handlers/dependencies/error mapping, JSON structured logs с redaction, проверку request ID, trusted proxy boundary, Origin/CSRF и Redis login limiter. Health проверяет зависимости с ограниченным timeout; public error не раскрывает DSN/path/traceback. Swagger/OpenAPI в VM защищены admin dependency.
- [ ] 4. Запустить тесты до PASS; проверить OpenAPI через authenticated client, statuses и schema; выполнить `ruff check`. Убедиться, что unauthorized запрос не выполняет прикладную операцию.
- [ ] 5. Проверить diff/staging и commit `feat(api): expose protected platform and authentication routes`.

## Task 4: Транзакционный аудит и минимальный worker

**Files:** Platform и Worker из карты; изменить `identity/bootstrap.py`, `api/auth.py`; `backend/tests/test_audit.py`, `test_outbox.py`, `test_worker.py`.

**Consumes:** Общая PostgreSQL transaction, Actor и request ID предыдущих задач.
**Produces:** `record_audit(session: Session, event: AuditInput) -> UUID`; `enqueue_event(session: Session, event_type: str, object_id: UUID, payload: dict) -> UUID`; `process_batch(factory: sessionmaker, limit: int) -> int`. AuditInput: actor_id (nullable для отказа входа), action, object_type, object_id, allowed details, request_id, request_source. Worker CLI запускает ограниченные batches и обновляет техническую свежесть состояния без вывода payload/секретов.

- [ ] 1. Написать тесты: bootstrap/login/logout создают audit; rollback не оставляет audit/outbox; payload/logs не содержат password/token; outbox виден worker только после commit; повторная доставка имеет ровно один receipt `(consumer,event_id)`; retry после crash сохраняет идемпотентность; terminal failure остаётся диагностируемым.
- [ ] 2. Запустить `uv run pytest tests/test_audit.py tests/test_outbox.py tests/test_worker.py -q`; проверить RED. Crash моделируется после transaction commit обработки и до transport acknowledgement.
- [ ] 3. Реализовать audit/outbox services и интегрировать с транзакциями auth/bootstrap. Отказ входа фиксируется отдельной committed audit-транзакцией до HTTP rejection, чтобы rollback попытки входа его не удалил. Worker использует PostgreSQL claiming/locking, bounded retry/backoff и unique receipts; начальный обработчик подтверждает platform admin-created event. Redis не хранит единственную копию события. Admin status показывает свежесть worker, без демонстрационных бизнес-операций.
- [ ] 4. Повторить task tests и `test_auth_api.py`/`test_bootstrap.py` до PASS; проверить restart worker с pending событием. `ruff check`.
- [ ] 5. Проверить diff/staging и commit `feat(platform): add transactional audit and outbox worker`.

## Task 5: Файловое хранилище и контракт импорта

**Files:** Storage/import из карты; `migration/examples/synthetic-record.json`; `backend/tests/test_storage.py`, `test_import_contract.py`.

**Consumes:** Проверенный storage root Settings; новый код не импортирует legacy packages.
**Produces:** `StorageProvider.put(key: str, source: BinaryIO) -> StoredFile`; `open_stream(key: str) -> ContextManager[BinaryIO]`; `get(key: str) -> bytes`; `exists(key: str) -> bool`; `stat(key: str) -> StoredFile`; `delete_if_allowed(key: str, *, allowed: bool) -> None`. StoredFile содержит storage_key, size, sha256; LocalFileStorage реализует provider. Версионированный import JSON Schema фиксирует contract из spec §7.

- [ ] 1. Написать tests: synthetic put/get/stat/hash roundtrip; повторный put не перезаписывает файл; `../`, абсолютный путь и symlink escape отвергаются; удаление без `allowed=True` запрещено. Контракт принимает два одинаковых source_id из разных source_database, отвергает неизвестную БД/невалидный UUID/file state и отличает no_path от unknown.
- [ ] 2. Запустить `uv run pytest tests/test_storage.py tests/test_import_contract.py -q`; проверить RED.
- [ ] 3. Реализовать provider с atomic publish/no overwrite и проверкой корня. Описать JSON Schema, synthetic example, source-qualified mapping key, сохранность target_id при повторе и history per run; перенести таблицу предварительного mapping в `migration/README.md` со ссылкой на архивные источники. Не создавать importer, не подключать source DB и не копировать реальные файлы.
- [ ] 4. Повторить tests до PASS; проверить что прерванная запись не публикует частичный файл, закрывает stream и удаляет только собственный temp. `ruff check`.
- [ ] 5. Проверить diff/staging и commit `feat(storage): add local provider and import contract`.

## Task 6: Frontend с реальным auth/API

**Files:** Frontend из карты; `frontend/index.html`, `frontend/.gitignore`; tests `frontend/src/api/client.test.ts`, `frontend/src/auth/AuthProvider.test.tsx`.

**Consumes:** API Task 3; `me` возвращает `id`, `display_name`, `roles`, `csrf_token`. Error envelope из spec. Backend ничего не знает о путях клиентских страниц.
**Produces:** `apiRequest<T>(path: string, init?: RequestInit) -> Promise<T>` с cookie credentials; `ApiError(status: number, code: string, message: string, requestId: string)`; auth context `loading | anonymous | authenticated | unavailable`, `login(login,password)`, `logout()`, `user`. Layout включает все восемь пунктов roadmap; неготовые разделы имеют статус «В разработке».

- [ ] 1. Зафиксировать совместимые React/TS/build/test versions и lockfile; проверить API React/Vite в Context7. Написать tests: 401 очищает auth, 503/network сохраняет состояние ошибки доступности, 204 logout не парсится как JSON, ошибка содержит requestId, login блокирует повторную отправку.
- [ ] 2. Запустить `npm ci` и `npm test -- --run`; проверить ожидаемый RED реализационных tests. Допускается закончить базовую build конфигурацию вместе с этими tests, без отдельного пустого scaffolding commit.
- [ ] 3. Реализовать компоненты и client: русские тексты, login, user/logout, topbar/sidebar, loading/error/focus states. В Workspace только действительные данные платформы и notice будущих разделов. Сетевой сбой не называется неверным паролем. Admin status зависит от серверного права, а не только видимости пункта.
- [ ] 4. Выполнить `npm test -- --run`, `npm run typecheck`, `npm run build`; ожидается PASS. После deployment в Task 8 через Playwright проверить login, logout, 401, keyboard/focus, узкий viewport, недоступность бизнес-разделов и отсутствие console errors.
- [ ] 5. Проверить diff/staging и commit `feat(web): add authenticated ACTI platform shell`.

## Task 7: Полный Compose, HTTPS и эксплуатационные команды

**Files:** Deployment из карты; `docs/DEPLOYMENT.md`; `backend/tests/test_deploy_contract.py`, расширить `test_preflight.py`.

**Consumes:** Backend image Tasks 1–5, frontend build Task 6, mount verifier Task 1 и закрытая конфигурация ВМ.
**Produces:** `deploy/stack.sh up|down|status` всегда проверяет mandatory mounts перед up; `acti-v3.service` использует RequiresMountsFor/ExecStartPre и управляет Compose. `backup.sh` создаёт consistent database dump; `restore-test.sh` восстанавливает его только в отдельную `_restore_test` БД. Пути/ожидаемые UUID/секреты принимаются извне checkout.

- [ ] 1. Написать tests/проверки: compose не публикует DB/Redis/API ports, bind create_host_path запрещён, migration failure блокирует API/worker, startup требует оба UUID, runtime restart policy не обходит systemd guard, restore guard отвергает рабочую БД. Проверить RED на ещё неполном deployment.
- [ ] 2. Завершить Compose services: PostgreSQL, Redis, migrate, API, worker, frontend/Nginx; healthchecks и dependency conditions. API/worker non-root, image labels содержат source SHA. Проверить реальные UID/GID и права только предназначенных подкаталогов томов. Не менять fstab/underlying protections.
- [ ] 3. Реализовать Nginx HTTPS, same-origin routing, trusted proxy headers, secrets files вне checkout. Получить/сформировать отдельный bootstrap certificate с SAN фактического адреса из local config; доверие клиента настроить явно. Подготовить versioned source archive без ignored файлов и секретов. Не переносить private TLS key в Git.
- [ ] 4. Реализовать stack/systemd/backup/restore guards и runbook с точными командами установки, preflight, init, bootstrap, startup, проверки, обновления и rollback. Описать последствия rollback image без schema downgrade и необходимость внешнего backup для реальных данных.
- [ ] 5. Выполнить `docker compose --env-file <private-env> -f deploy/compose.yaml config --quiet`, `docker compose ... build`, deployment tests и `ruff check`. Проверку неверного UUID выполнить подстановкой ожидаемого значения, без unmount/reboot. Ожидается отказ до запуска пишущих services, без fallback файлов.
- [ ] 6. Проверить diff/staging и commit `feat(deploy): add guarded Compose deployment and recovery tools`.

## Task 8: Развёртывание и приёмка на основной ВМ

**Files:** `docs/architecture/TASK_001_ACCEPTANCE.md`, `docs/DEPLOYMENT.md`, `README.md`, `project.toml`; только локально — игнорируемые context/settings с реальным состоянием.

**Consumes:** Все task commits; утверждённые spec и plan; основной SSH alias и закрытая конфигурация. Runtime-команды ниже используют фактический private env path, не публикуемый в Git.
**Produces:** Функционирующая платформа и sanitised acceptance report с точными source SHA, командами и результатами. Локальный отчёт содержит адрес доступа и инструкции начального входа без пароля.

- [ ] 1. Повторно проверить HEAD/diff, SSH, mount UUID/права, Docker/Compose и resource availability. Развернуть именно committed source Task 7, записать image/source SHA. `stack.sh up` запускает migrations и services; bootstrap admin выполнить скрытым вводом. Пользователь задаёт пароль через защищённый терминал; для автоматизированной приёмки использовать отдельный временный credential, затем отозвать его сессию/сменить credential без вывода в отчёт.
- [ ] 2. Выполнить весь новый backend suite `uv run pytest -q`, `uv run ruff check app tests`, `uv run alembic check`; frontend `npm test -- --run`, `npm run typecheck`, `npm run build`. Исправлять ошибки до повторной проверки затронутой области. Несвязанные legacy checks не добавлять.
- [ ] 3. Через Nginx проверить live/ready, anonymous 401, user 403/admin 200, login/me/logout, CSRF, ошибки и request ID. Проверить `docker compose ps`, отсутствие опубликованных DB/Redis/API ports и работу worker. Подтвердить TLS в браузере с настроенным доверием, без игнорирования ошибок сертификата как критерия приёмки.
- [ ] 4. Через существующий Playwright MCP в отдельном tab проверить реальный UI: login -> текущий user -> разделы/статусы -> admin status -> logout -> защищённый API 401. Проверить неверный пароль, network/503 UI, клавиатуру и узкий экран; сохранить только обезличенные evidence. Не менять пользовательские открытые формы/вкладки.
- [ ] 5. Снять контрольные user ID и audit/event counts без credential material; пересоздать контейнеры без удаления persistent data (`down` без `--volumes`, затем guarded up). Повторить вход, сравнить user ID и сохранённые audit rows; проверить повторный Alembic head и worker recovery.
- [ ] 6. Сделать dump новой БД, восстановить в отдельную `_restore_test` БД на PostgreSQL-томе, сравнить Alembic revision, user IDs и audit counts; удалить только явно созданную disposable restore DB. Рабочую БД не перезаписывать.
- [ ] 7. Заполнить acceptance report фактическими результатами §10 spec. Обновить README/deployment status и локальный context только по доказанному состоянию. Проверить `git diff --exit-code <baseline> -- legacy/v2 docs/ACTI_V3_VISION_AND_ROADMAP.md`, `git diff --check` и отсутствие private files в stage. Commit `docs(platform): record deployed foundation acceptance`.
- [ ] 8. Итог пользователю: полный SHA каждого task commit, фактический адрес приложения, реализованные функции, выполненные команды/результаты, ограничения, остаток для Task 002. Не объявлять успех при pending browser/persistence/migration gate и не начинать следующий этап.

## Самопроверка плана

- Spec §§1–3: границы и архитектура сохранены во всех tasks; окружение/версии — Task 1.
- Spec §§4–5: схема — Task 1, identity — Task 2, HTTP — Task 3, audit/outbox — Task 4.
- Spec §§6–7: provider, идентификаторы, версии и provenance/mapping — Task 5.
- Spec §8: UI и состояния — Task 6; фактический браузер — Task 8.
- Spec §9: mount guards/HTTPS/Compose/runbook — Tasks 1 и 7; backup/restore — Tasks 7–8.
- Spec §10: автоматические tests распределены по владельцам; deployment/browser/persistence/restore — Task 8.
- Signatures identity/API/platform/storage согласованы между tasks. Транзакцией владеет вызывающая прикладная операция; helpers не делают скрытый commit.
- Все пять Review Focus условий имеют проверку в соответствующей задаче. Ожидаемые PASS выше — критерии будущего исполнения, не заявления о выполнении.

## Способ исполнения

Рекомендация: native — реализация основным агентом в этом чате, по одному
проверяемому результату, без независимых аудитов и многочисленных ревью.
Tasks последовательно зависят от DB/identity/API контрактов; делегирование
каждого task добавит передачу контекста без необходимости для этого фундамента.
До начала реализации пользователь проверяет этот план и выбирает способ исполнения.
