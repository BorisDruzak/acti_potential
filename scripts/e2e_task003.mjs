/** Real HTTPS Task 003 acceptance. Private configuration stays outside Git.
 * Run: node scripts/e2e_task003.mjs <ignored-config.json>
 * Config keys: origin, adminCredentialsFile, playwrightModule, outputDir.
 * Uses isolated Chromium contexts and actual HTTP/PostgreSQL; no routes/mocks.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomBytes } from 'node:crypto';

export async function runTask003({ browser, origin, admin, outputDir }) {
  assert.equal(new URL(origin).protocol, 'https:');
  await fs.mkdir(outputDir, { recursive: true });
  const suffix = Date.now().toString(36);
  const code = `task003_${suffix}`;
  const title = `Task003 ${suffix}`;
  const results = [];
  const contexts = [];
  const errors = [];
  const accounts = [];
  const result = (name, details = {}) => {
    results.push({ name, status: 'PASS', ...details });
    console.log(`PASS ${name}`);
  };
  async function pageFor() {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: false });
    contexts.push(context);
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => errors.push(error.name));
    return page;
  }
  async function json(page, endpoint, method = 'GET', data, expected = 200) {
    const me = method === 'GET' ? null : await page.context().request.get(`${origin}/api/v1/users/me`);
    const actor = me?.ok() ? await me.json() : null;
    const response = await page.context().request.fetch(`${origin}/api/v1${endpoint}`, {
      method, ...(data === undefined ? {} : { data }),
      headers: { Origin: origin, ...(actor ? { 'X-CSRF-Token': actor.csrf_token } : {}) },
    });
    assert.equal(response.status(), expected, `${method} ${endpoint.split('?')[0]} status`);
    return response.status() === 204 ? null : response.json();
  }
  async function login(page, credentials) {
    await page.goto(origin);
    await page.getByRole('textbox', { name: 'Логин', exact: true }).fill(credentials.login);
    await page.getByLabel('Пароль', { exact: true }).fill(credentials.password);
    await page.getByRole('button', { name: 'Войти в рабочее пространство' }).click();
    await page.getByRole('button', { name: 'Выйти', exact: true }).waitFor();
  }
  async function mutation(page, endpoint, action, method = 'POST', expected = 201) {
    const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1${endpoint}` && response.request().method() === method);
    const [response] = await Promise.all([responsePromise, action()]);
    assert.equal(response.status(), expected, `${method} ${endpoint} UI status`);
    return expected === 204 ? null : response.json();
  }
  async function download(page, format, prefix) {
    const promise = page.waitForEvent('download');
    await page.getByRole('button', { name: `Экспорт ${format.toUpperCase()}`, exact: true }).click();
    const value = await promise;
    assert.equal(await value.failure(), null);
    const target = path.join(outputDir, `${prefix}.${format}`);
    await value.saveAs(target);
    assert.ok((await fs.stat(target)).size > 20);
    return target;
  }
  let ap;
  let legacyAccess;
  try {
    ap = await pageFor();
    await login(ap, admin);
    const status = await json(ap, '/admin/platform');
    result('administrator real login and source', { source_sha: status.source_sha });

    // Configuration is created in the administrator UI, not seeded schemas.
    await ap.getByRole('button', { name: 'Справочники', exact: true }).click();
    await ap.getByRole('button', { name: 'Создать справочник', exact: true }).click();
    await ap.getByLabel('Название справочника', { exact: true }).fill(`${title} справочник`);
    await ap.getByLabel('Код справочника', { exact: true }).fill(code);
    const ref = await mutation(ap, '/references', () => ap.locator('form').getByRole('button', { name: 'Создать справочник', exact: true }).click());
    await ap.locator('summary').filter({ hasText: /^Добавить значение$/ }).click();
    await ap.getByLabel('Новое значение', { exact: true }).fill('Первое значение');
    await ap.getByLabel('Код значения', { exact: true }).fill('0001');
    await ap.getByLabel('Описание значения', { exact: true }).fill('Синтетическое значение Task003');
    await ap.getByLabel('Порядок', { exact: true }).fill('1');
    const item = await mutation(ap, `/references/${ref.id}/items`, () => ap.getByRole('button', { name: 'Добавить значение', exact: true }).click());
    const csvImport = Buffer.from('code,label,description,sort_order,active\n0002,Второе значение,Импорт CSV,2,true\n');
    await mutation(ap, `/references/${ref.id}/import`, () => ap.getByLabel('Импорт CSV / XLSX').setInputFiles({ name: 'synthetic.csv', mimeType: 'text/csv', buffer: csvImport }), 'POST', 200);
    const refCsv = await download(ap, 'csv', 'reference');
    const refXlsx = await download(ap, 'xlsx', 'reference');
    assert.match(await fs.readFile(refCsv, 'utf8'), /0001/);
    await mutation(ap, `/references/${ref.id}/import`, () => ap.getByLabel('Импорт CSV / XLSX').setInputFiles(refXlsx), 'POST', 200);
    await ap.getByRole('button', { name: 'Журнал изменений', exact: true }).click();
    await ap.getByRole('heading', { name: 'Журнал изменений справочника' }).waitFor();
    result('reference UI management CSV and XLSX roundtrip history');

    await ap.getByRole('button', { name: 'Типы документов', exact: true }).click();
    await ap.getByRole('button', { name: 'Создать тип', exact: true }).click();
    await ap.getByLabel('Название типа', { exact: true }).fill(title);
    await ap.getByLabel('Системный код', { exact: true }).fill(code);
    const type = await mutation(ap, '/document-types', () => ap.getByRole('button', { name: 'Создать', exact: true }).click());
    await ap.getByRole('heading', { name: 'Конструктор формы', exact: true }).waitFor();
    const properties = ap.locator('.property-panel');
    async function addField(kind, key, label, configure) {
      await ap.getByRole('combobox', { name: 'Добавляемый тип', exact: true }).selectOption(kind);
      await ap.getByRole('button', { name: '+ Добавить поле', exact: true }).first().click();
      await properties.getByLabel('Название поля', { exact: true }).fill(label);
      await properties.getByLabel('Системный ключ', { exact: false }).fill(key);
      await properties.getByLabel('Доступно в фильтрах', { exact: true }).check();
      await properties.getByLabel('Доступно в экспорте', { exact: true }).check();
      if (configure) await configure(properties);
      console.log(`UI field configured: ${key}`);
    }
    await addField('text', 'number', 'Номер Task003', async p => {
      await p.getByRole('combobox', { name: 'Системный реквизит', exact: true }).selectOption('reg_number');
      await p.getByLabel('Обязательное', { exact: true }).check();
      await p.getByLabel('Регулярное выражение', { exact: true }).fill('^[0-9]{4}$');
    });
    await addField('text', 'title', 'Название Task003', async p => {
      await p.getByRole('combobox', { name: 'Системный реквизит', exact: true }).selectOption('title');
      await p.getByLabel('Обязательное', { exact: true }).check();
    });
    await addField('date', 'date', 'Дата Task003');
    await addField('decimal', 'amount', 'Сумма Task003', async p => {
      await p.getByLabel('Минимум', { exact: true }).fill('0');
      await p.getByLabel('Максимум', { exact: true }).fill('1000');
    });
    await addField('multi_reference', 'choices', 'Выбор Task003', async p => {
      await p.getByRole('combobox', { name: 'Справочник', exact: true }).selectOption(ref.id);
    });
    await addField('boolean', 'trigger', 'Условие Task003');
    await addField('text', 'detail', 'Деталь Task003', async p => {
      for (const legend of ['Условие видимости', 'Условие обязательности']) {
        const rule = p.getByRole('group', { name: legend, exact: true });
        await rule.getByRole('combobox', { name: 'Поле условия', exact: true }).selectOption('trigger');
        await rule.getByRole('combobox', { name: 'Оператор', exact: true }).selectOption('eq');
        await rule.getByLabel('Значение', { exact: false }).fill('true');
      }
    });
    await addField('text', 'secret', 'Скрыто Task003', async p => {
      await p.getByRole('group', { name: 'Роли для просмотра', exact: true }).getByLabel('editor', { exact: true }).check();
      await p.getByRole('group', { name: 'Роли для изменения', exact: true }).getByLabel('editor', { exact: true }).check();
    });
    await addField('group', 'rows', 'Строки Task003');
    await ap.getByRole('combobox', { name: 'Добавляемый тип', exact: true }).selectOption('text');
    await ap.getByRole('button', { name: '+ Поле в группу', exact: true }).click();
    await ap.getByRole('combobox', { name: 'Перейти к полю', exact: true }).selectOption('0:8:0');
    await properties.getByLabel('Название поля', { exact: true }).fill('Строка Task003');
    await properties.getByLabel('Системный ключ', { exact: false }).fill('item');
    await properties.getByLabel('Доступно в экспорте', { exact: true }).check();
    const listDetails = ap.locator('details').filter({ has: ap.getByText('Представление списка документов', { exact: true }) });
    await listDetails.locator('summary').click();
    for (const name of ['Номер Task003', 'Название Task003', 'Сумма Task003', 'Выбор Task003', 'Строки Task003', 'Скрыто Task003']) {
      await listDetails.getByLabel(name, { exact: true }).check();
    }
    await listDetails.getByLabel('Заголовок: amount', { exact: true }).fill('Сумма, настроенная администратором');
    await ap.getByRole('button', { name: 'Как редактор', exact: true }).click();
    const preview = ap.locator('.preview');
    assert.equal(await preview.getByLabel('Деталь Task003', { exact: false }).count(), 0);
    await preview.getByLabel('Условие Task003', { exact: false }).selectOption('true');
    await preview.getByLabel('Деталь Task003', { exact: false }).waitFor();
    await ap.getByRole('button', { name: 'Закрыть предпросмотр' }).click();
    const versions = await json(ap, `/document-types/${type.id}/versions`);
    const v1 = versions[0];
    await mutation(ap, `/form-schemas/${v1.id}/publish`, () => ap.getByRole('button', { name: 'Опубликовать', exact: true }).click(), 'POST', 200);
    result('administrator builds publishes arbitrary form conditions group and list columns');

    await ap.getByRole('button', { name: 'Реестры', exact: true }).click();
    await ap.getByRole('button', { name: 'Создать реестр', exact: true }).click();
    await ap.getByLabel('Название реестра', { exact: true }).fill(title);
    await ap.getByLabel('Код реестра', { exact: true }).fill(code);
    await ap.getByRole('group', { name: 'Доступные типы документов' }).getByLabel(title, { exact: true }).check();
    const registry = await mutation(ap, '/registries', () => ap.getByRole('button', { name: 'Сохранить реестр', exact: true }).click());

    const rolePages = {};
    for (const role of ['editor', 'viewer']) {
      const temporary = randomBytes(24).toString('base64url');
      const password = randomBytes(24).toString('base64url');
      const account = await json(ap, '/admin/users', 'POST', { login: `${code}_${role}`, display_name: `${title} ${role}`, password: temporary, roles: [role] }, 201);
      accounts.push(account.id);
      rolePages[role] = await pageFor();
      await login(rolePages[role], { login: `${code}_${role}`, password: temporary });
      await rolePages[role].getByLabel('Текущий пароль', { exact: true }).fill(temporary);
      await rolePages[role].getByLabel('Новый пароль', { exact: true }).fill(password);
      await rolePages[role].getByLabel('Повторите новый пароль', { exact: true }).fill(password);
      await rolePages[role].getByRole('button', { name: 'Изменить пароль', exact: true }).click();
      await rolePages[role].getByRole('heading', { name: 'Мои реестры', exact: true }).waitFor();
      account.permissions = role === 'editor' ? ['read', 'create', 'update', 'download', 'upload'] : ['read', 'download'];
      rolePages[role].account = account;
    }
    const administrator = await json(ap, '/users/me');
    await json(ap, `/admin/access/${registry.id}`, 'PUT', { entries: [{ user_id: administrator.id, permissions: ['read', 'create', 'update', 'download', 'upload'] }, ...Object.values(rolePages).map(p => ({ user_id: p.account.id, permissions: p.account.permissions }))] });
    for (const p of Object.values(rolePages)) await p.reload();
    const ep = rolePages.editor;
    const vp = rolePages.viewer;
    await ep.getByRole('button', { name: title, exact: true }).click();
    await ep.getByRole('button', { name: 'Создать документ', exact: true }).click();
    await ep.getByRole('combobox', { name: 'Тип документа', exact: true }).selectOption(type.id);
    await ep.getByLabel('Номер Task003', { exact: false }).fill('0012');
    await ep.getByLabel('Название Task003', { exact: false }).fill(`${title} карточка`);
    await ep.getByLabel('Дата Task003', { exact: false }).fill('2026-10-15');
    await ep.getByLabel('Сумма Task003', { exact: false }).fill('12.50');
    await ep.getByLabel('Выбор Task003', { exact: false }).selectOption([item.id]);
    await ep.getByLabel('Скрыто Task003', { exact: false }).fill('TASK003-SECRET');
    assert.equal(await ep.getByLabel('Деталь Task003', { exact: false }).count(), 0);
    await ep.getByLabel('Условие Task003', { exact: false }).selectOption('true');
    assert.equal(await ep.getByLabel('Деталь Task003', { exact: false }).getAttribute('required'), '');
    await ep.getByRole('button', { name: 'Создать документ', exact: true }).click();
    assert.equal(await ep.getByLabel('Деталь Task003', { exact: false }).evaluate(el => el.validity.valueMissing), true);
    await ep.getByLabel('Деталь Task003', { exact: false }).fill('Условное значение');
    await ep.getByRole('button', { name: 'Добавить: Строки Task003', exact: true }).click();
    await ep.getByLabel('Строка Task003', { exact: false }).fill('=FORMULA-SAFE');
    const card = await mutation(ep, '/documents', () => ep.getByRole('button', { name: 'Создать документ', exact: true }).click());
    result('editor real form conditional requirement repeat group save PostgreSQL');
    let withFiles;
    for (const version of [1, 2]) {
      await ep.getByRole('button', { name: 'Файлы и версии', exact: true }).click();
      withFiles = await mutation(ep, `/documents/${card.id}/files`, () => ep.getByLabel('Добавить файл или следующую версию', { exact: false }).setInputFiles({ name: `Task003-original-${version}.txt`, mimeType: 'text/plain', buffer: Buffer.from(`Synthetic Task003 original version ${version}\n`) }));
    }
    assert.equal(withFiles.files.length, 2);
    result('editor uploads two immutable synthetic originals');
    await ep.getByRole('button', { name: 'К списку', exact: true }).click();
    await ep.getByRole('combobox', { name: 'Тип', exact: true }).selectOption(type.id);
    await ep.getByRole('combobox', { name: 'Дополнительный фильтр', exact: true }).selectOption('amount');
    await ep.getByRole('combobox', { name: 'Сравнение', exact: true }).selectOption('between');
    await ep.getByLabel('Значение фильтра', { exact: false }).fill('12');
    await ep.getByLabel('Верхняя граница', { exact: true }).fill('13');
    await ep.getByRole('button', { name: 'Найти', exact: true }).click();
    await ep.getByText('Найдено: 1', { exact: true }).waitFor();
    await download(ep, 'csv', 'documents-editor');
    await download(ep, 'xlsx', 'documents-editor');
    result('editor UI range search CSV XLSX exports');

    const forbidden = await json(ap, '/documents', 'POST', { registry_id: registry.id, document_type_id: type.id, schema_version_id: v1.id, values: { number: '0013', title: `${title} запрещено`, amount: '12.75', trigger: false } }, 201);
    await json(ap, `/documents/${forbidden.id}/acl`, 'PUT', { entries: [{ user_id: ep.account.id, permissions: ['read', 'update'] }] });
    await json(vp, `/documents/${forbidden.id}?mode=viewer`, 'GET', undefined, 404);
    await json(vp, `/documents/${card.id}`, 'PUT', { revision: card.revision, values: {} }, 403);
    await json(vp, '/admin/users', 'GET', undefined, 403);
    await json(ep, '/admin/users', 'GET', undefined, 403);
    await json(ep, `/form-schemas/${v1.id}`, 'PUT', { schema: v1.schema }, 403);
    await json(vp, `/documents?registry_id=${registry.id}&filters=${encodeURIComponent(JSON.stringify({ secret: 'TASK003-SECRET' }))}`, 'GET', undefined, 422);
    await json(vp, `/documents/export?registry_id=${registry.id}&columns=secret`, 'GET', undefined, 422);
    const readerCard = await json(vp, `/documents/${card.id}?mode=viewer`);
    assert.equal('secret' in readerCard.values, false);
    assert.equal(JSON.stringify(readerCard).includes('TASK003-SECRET'), false);
    result('editor reader direct API authorization row and column ACL');

    await vp.getByRole('button', { name: title, exact: true }).click();
    await vp.getByRole('combobox', { name: 'Тип', exact: true }).selectOption(type.id);
    await vp.getByRole('combobox', { name: 'Дополнительный фильтр', exact: true }).selectOption('amount');
    await vp.getByRole('combobox', { name: 'Сравнение', exact: true }).selectOption('between');
    await vp.getByLabel('Значение фильтра', { exact: false }).fill('12');
    await vp.getByLabel('Верхняя граница', { exact: true }).fill('13');
    await vp.getByRole('button', { name: 'Найти', exact: true }).click();
    await vp.getByText('Найдено: 1', { exact: true }).waitFor();
    assert.equal(await vp.getByRole('button', { name: 'Создать документ', exact: true }).count(), 0);
    assert.equal(await vp.getByRole('columnheader', { name: 'Скрыто Task003', exact: true }).count(), 0);
    await vp.getByRole('columnheader', { name: 'Сумма, настроенная администратором' }).waitFor();
    const documentsCsv = await download(vp, 'csv', 'documents-reader');
    await download(vp, 'xlsx', 'documents-reader');
    const csv = await fs.readFile(documentsCsv, 'utf8');
    assert.ok(csv.includes('0012'));
    assert.ok(csv.includes('Первое значение'));
    assert.equal(csv.includes('TASK003-SECRET'), false);
    assert.equal(csv.includes('0013'), false);
    assert.equal(csv.includes('запрещено'), false);
    await vp.getByText('Сохранённые фильтры и сортировки', { exact: true }).click();
    await vp.getByLabel('Название представления', { exact: true }).fill('Task003 диапазон');
    await vp.getByRole('button', { name: 'Сохранить текущий поиск', exact: true }).click();
    await vp.reload();
    await vp.getByRole('button', { name: title, exact: true }).click();
    await vp.getByText('Сохранённые фильтры и сортировки', { exact: true }).click();
    await vp.getByRole('button', { name: 'Task003 диапазон', exact: true }).click();
    await vp.getByText('Найдено: 1', { exact: true }).waitFor();
    await vp.getByRole('row', { name: /Выбрать документ/ }).click();
    assert.equal(await vp.getByRole('button', { name: 'Редактировать', exact: true }).count(), 0);
    assert.equal((await vp.locator('main').innerText()).includes('TASK003-SECRET'), false);
    await vp.screenshot({ path: path.join(outputDir, 'reader-card.png'), fullPage: true });
    await vp.getByRole('button', { name: 'Файлы и версии', exact: true }).click();
    for (const version of [1, 2]) {
      const event = vp.waitForEvent('download');
      await vp.locator('.file-line').filter({ hasText: `Task003-original-${version}.txt` }).getByRole('button', { name: 'Скачать', exact: true }).click();
      const value = await event;
      const target = path.join(outputDir, `original-${version}.txt`);
      await value.saveAs(target);
      assert.equal(await fs.readFile(target, 'utf8'), `Synthetic Task003 original version ${version}\n`);
    }
    await json(ap, `/documents/${card.id}/acl`, 'PUT', { entries: [{ user_id: ep.account.id, permissions: ['read', 'update'] }] });
    await json(vp, `/files/${withFiles.files[0].id}/download`, 'GET', undefined, 404);
    await json(ap, `/documents/${card.id}/acl`, 'PUT', { entries: [] });
    result('reader downloads old and current originals and denied file ACL');
    result('reader UI range search saved views configured columns CSV XLSX restricted export');

    await ap.getByRole('button', { name: 'Типы документов', exact: true }).click();
    await ap.getByRole('combobox', { name: 'Тип документа', exact: true }).selectOption(type.id);
    const v2 = await mutation(ap, `/document-types/${type.id}/versions`, () => ap.getByRole('button', { name: 'Новая версия', exact: true }).click());
    await addField('text', 'later', 'Только версия 2');
    await mutation(ap, `/form-schemas/${v2.id}/publish`, () => ap.getByRole('button', { name: 'Опубликовать', exact: true }).click(), 'POST', 200);
    const historic = await json(vp, `/documents/${card.id}?mode=viewer`);
    assert.equal(historic.schema_version_id, v1.id);
    assert.equal(JSON.stringify(historic.schema).includes('later'), false);
    await vp.reload();
    await vp.getByRole('button', { name: title, exact: true }).click();
    await vp.getByRole('row', { name: /Выбрать документ/ }).click();
    assert.equal((await vp.locator('main').innerText()).includes('Только версия 2'), false);
    result('published schema v2 retains historical v1 card');

    await ap.getByRole('button', { name: 'Справочники', exact: true }).click();
    await ap.getByRole('combobox', { name: 'Справочник', exact: true }).selectOption(ref.id);
    await ap.getByRole('button', { name: 'Изменить: Первое значение', exact: true }).click();
    await ap.locator('.reference-list').getByLabel('Активно', { exact: true }).uncheck();
    await mutation(ap, `/references/${ref.id}/items/${item.id}`, () => ap.getByRole('button', { name: 'Сохранить значение', exact: true }).click(), 'PATCH', 200);
    await vp.reload();
    await vp.getByRole('button', { name: title, exact: true }).click();
    await vp.getByRole('row', { name: /Выбрать документ/ }).click();
    await vp.getByText('Первое значение', { exact: false }).first().waitFor();
    result('inactive reference retains historical display');

    // Reuse only existing synthetic v2 acceptance cards; source systems are untouched.
    const types = await json(ap, '/document-types');
    const legacy = types.find(value => value.code === 'acti-v2');
    assert.ok(legacy, 'ACTI v2 type exists');
    const legacyDocs = await json(ap, `/documents?document_type_id=${legacy.id}&page_size=1`);
    assert.ok(legacyDocs.items.length, 'existing synthetic ACTI v2 card exists');
    const legacyRegistry = legacyDocs.items[0].registry_id;
    const existing = await json(ap, `/admin/access/${legacyRegistry}`);
    const existingEntries = existing.entries.filter(value => !accounts.includes(value.user_id));
    legacyAccess = { registry: legacyRegistry, entries: existing.entries };
    await json(ap, `/admin/access/${legacyRegistry}`, 'PUT', { entries: [...existingEntries, ...Object.values(rolePages).map(p => ({ user_id: p.account.id, permissions: p.account.permissions }))] });
    const legacyCard = await json(vp, `/documents/${legacyDocs.items[0].id}?mode=viewer`);
    assert.ok(legacyCard.schema.sections.length >= 6);
    await vp.reload();
    const registries = await json(ap, '/registries');
    await vp.getByRole('button', { name: registries.find(value => value.id === legacyRegistry).name, exact: true }).click();
    await vp.getByRole('row', { name: /Выбрать документ/ }).first().click();
    await vp.getByText('Основные сведения', { exact: false }).first().waitFor();
    await vp.screenshot({ path: path.join(outputDir, 'acti-v2-reader.png'), fullPage: true });
    result('ACTI v2 published six section card reference rendering');
    await vp.setViewportSize({ width: 390, height: 844 });
    assert.ok(await vp.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    result('reader mobile viewport no overflow');
    for (const p of [ep, vp]) {
      await p.goto(`${origin}/admin`);
      await p.waitForURL(url => url.pathname !== '/admin');
      assert.notEqual(new URL(p.url()).pathname, '/admin');
      await p.getByRole('button', { name: 'Выйти', exact: true }).click();
      await p.getByRole('heading', { name: 'Вход в ACTI', exact: true }).waitFor();
      await json(p, '/users/me', 'GET', undefined, 401);
    }
    assert.deepEqual(errors, [], 'No uncaught application JavaScript errors');
    result('roles admin navigation denied and real logout');
    return { status: 'PASS', source_sha: status.source_sha, scenarios: results, synthetic: { registry_id: registry.id, type_id: type.id, reference_id: ref.id, document_id: card.id, restricted_document_id: forbidden.id } };
  } catch (error) {
    await fs.writeFile(path.join(outputDir, 'failure.txt'), String(error.message).split(admin.password).join('[redacted]'));
    await ap?.screenshot({ path: path.join(outputDir, 'failure-admin.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    const cleanupFailures = [];
    if (ap && legacyAccess) {
      try {
        const current = await json(ap, `/admin/access/${legacyAccess.registry}`);
        await json(ap, `/admin/access/${legacyAccess.registry}`, 'PUT', {
          entries: current.entries.filter(entry => !accounts.includes(entry.user_id)),
        });
      } catch { cleanupFailures.push('temporary registry grants'); }
    }
    if (ap) for (const id of accounts) {
      try { await json(ap, `/admin/users/${id}`, 'PATCH', { active: false }); }
      catch { cleanupFailures.push('synthetic account'); }
    }
    for (const context of contexts) await context.close();
    await fs.writeFile(path.join(outputDir, 'scenarios.json'), JSON.stringify(results, null, 2));
    assert.deepEqual(cleanupFailures, [], 'Synthetic fixture cleanup must complete');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  let browser;
  try {
    const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
    const admin = JSON.parse(await fs.readFile(config.adminCredentialsFile, 'utf8'));
    const { chromium } = await import(config.playwrightModule ? pathToFileURL(config.playwrightModule).href : 'playwright');
    browser = await chromium.launch({ headless: true, ...(config.executablePath ? { executablePath: config.executablePath } : {}) });
    const receipt = await runTask003({ browser, origin: config.origin, admin, outputDir: config.outputDir });
    await fs.writeFile(path.join(config.outputDir, 'acceptance.json'), JSON.stringify(receipt, null, 2));
    console.log(`Task003 real browser acceptance PASS: ${receipt.scenarios.length} scenarios.`);
  } catch (error) {
    // Deliberately exclude call logs, responses and credential values.
    console.error(`Task003 browser acceptance FAIL: ${error.name}: ${error.message.split('\n')[0]}`);
    process.exitCode = 1;
  } finally { await browser?.close(); }
}
