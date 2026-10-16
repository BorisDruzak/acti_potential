/** Task 004 acceptance against the real HTTPS deployment.
 * node scripts/e2e_task004.mjs <ignored-config.json> [--verify-persistence]
 * Private config: origin, adminCredentialsFile, playwrightModule, outputDir.
 * Each run uses fresh synthetic series/accounts; never connects to source systems.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomBytes } from 'node:crypto';

function key(prefix) { return `${prefix}-${randomBytes(16).toString('hex')}`; }

async function helpers(browser, origin, outputDir) {
  assert.equal(new URL(origin).protocol, 'https:');
  await fs.mkdir(outputDir, { recursive: true });
  const contexts = [];
  const runtimeErrors = [];
  const results = [];
  async function pageFor() {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: false });
    contexts.push(context);
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => runtimeErrors.push(error.name));
    return page;
  }
  async function json(page, endpoint, method = 'GET', data, expected = 200) {
    const response = await page.evaluate(async ({ endpoint, method, data }) => {
      const me = method === 'GET' ? null : await (await fetch('/api/v1/users/me')).json();
      const response = await fetch('/api/v1' + endpoint, {
        method, headers: { Accept: 'application/json', ...(data === undefined ? {} : { 'Content-Type': 'application/json' }), ...(me ? { 'X-CSRF-Token': me.csrf_token } : {}) },
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      });
      return { status: response.status, body: await response.json().catch(() => null) };
    }, { endpoint, method, data });
    assert.equal(response.status, expected, `${method} ${endpoint.split('?')[0]} status`);
    return response.body;
  }
  async function login(page, credentials) {
    await page.goto(origin);
    await page.getByRole('textbox', { name: 'Логин', exact: true }).fill(credentials.login);
    await page.getByLabel('Пароль', { exact: true }).fill(credentials.password);
    await page.getByRole('button', { name: 'Войти в рабочее пространство' }).click();
    await page.getByRole('button', { name: 'Выйти', exact: true }).waitFor();
  }
  async function mutation(page, endpoint, action, method = 'POST', expected = 200) {
    const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1' + endpoint && response.request().method() === method);
    const [response] = await Promise.all([pending, action()]);
    assert.equal(response.status(), expected, `${method} ${endpoint} UI status`);
    return expected === 204 ? null : response.json();
  }
  async function download(page, buttonName, filename) {
    const pending = page.waitForEvent('download');
    await page.getByRole('button', { name: buttonName, exact: true }).click();
    const value = await pending;
    assert.equal(await value.failure(), null);
    const target = path.join(outputDir, filename);
    await value.saveAs(target);
    assert.ok((await fs.stat(target)).size > 20);
    return target;
  }
  function pass(name, details = {}) {
    results.push({ name, status: 'PASS', ...details });
    console.log(`PASS ${name}`);
  }
  return { pageFor, json, login, mutation, download, pass, contexts, runtimeErrors, results };
}

export async function runTask004({ browser, origin, admin, outputDir }) {
  const h = await helpers(browser, origin, outputDir);
  const { pageFor, json, login, mutation, download, pass } = h;
  const suffix = Date.now().toString(36);
  const code = `task004_${suffix}`;
  const title = `Task004 ${suffix}`;
  const accountIds = [];
  const sensitive = [admin.password];
  const temporaryRegistryGrants = new Set();
  let ap;
  try {
    ap = await pageFor();
    await login(ap, admin);
    const platform = await json(ap, '/admin/platform');
    const administrator = await json(ap, '/users/me');
    pass('administrator actual HTTPS login and source', { source_sha: platform.source_sha });
    const type = await json(ap, '/document-types', 'POST', { code, name: title }, 201);
    const registry = await json(ap, '/registries', 'POST', { code, name: title, document_type_ids: [type.id] }, 201);
    const roles = {};
    for (const [name, role] of [['editor1', 'editor'], ['editor2', 'editor'], ['reader', 'viewer']]) {
      const temporary = randomBytes(24).toString('base64url');
      const password = randomBytes(24).toString('base64url');
      sensitive.push(temporary, password);
      const account = await json(ap, '/admin/users', 'POST', { login: `${code}_${name}`, display_name: `${title} ${name}`, password: temporary, roles: [role] }, 201);
      accountIds.push(account.id);
      const page = await pageFor();
      await login(page, { login: `${code}_${name}`, password: temporary });
      await page.getByLabel('Текущий пароль', { exact: true }).fill(temporary);
      await page.getByLabel('Новый пароль', { exact: true }).fill(password);
      await page.getByLabel('Повторите новый пароль', { exact: true }).fill(password);
      await page.getByRole('button', { name: 'Изменить пароль', exact: true }).click();
      await page.getByRole('heading', { name: 'Мои реестры', exact: true }).waitFor();
      roles[name] = { page, account, permissions: role === 'editor' ? ['read', 'create', 'update', 'download', 'upload'] : ['read', 'download'] };
    }
    await json(ap, `/admin/access/${registry.id}`, 'PUT', { entries: [{ user_id: administrator.id, permissions: ['read', 'create', 'update', 'download', 'upload'] }, ...Object.values(roles).map(value => ({ user_id: value.account.id, permissions: value.permissions }))] });
    for (const value of Object.values(roles)) await value.page.reload();
    const ep = roles.editor1.page;
    const second = roles.editor2.page;
    const vp = roles.reader.page;

    await ap.getByRole('navigation').getByRole('button', { name: 'Серии номеров', exact: true }).click();
    async function createSeriesUI(tag, initial, step) {
      await ap.getByRole('button', { name: 'Создать серию номеров', exact: true }).click();
      const form = ap.locator('form').filter({ has: ap.getByRole('heading', { name: 'Новая серия номеров' }) });
      await form.getByLabel('Название серии', { exact: true }).fill(`${title} ${tag}`);
      await form.getByLabel('Код серии', { exact: true }).fill(`${code}_${tag.toLowerCase()}`);
      await form.getByRole('combobox', { name: 'Реестр серии', exact: true }).selectOption(registry.id);
      await form.getByRole('group', { name: 'Допустимые типы документов' }).getByLabel(title, { exact: true }).check();
      await form.getByRole('combobox', { name: 'Правило периода', exact: true }).selectOption('manual');
      await form.getByLabel('Текущий период', { exact: true }).fill('2026');
      await form.getByLabel('Начальное значение', { exact: true }).fill(String(initial));
      await form.getByLabel('Шаг нумерации', { exact: true }).fill(String(step));
      await form.getByLabel('Префикс', { exact: true }).fill(`${tag}-`);
      await form.getByLabel('Формат номера', { exact: true }).fill('{prefix}{period}/{ordinal}');
      await form.getByLabel('Максимум номеров за запрос', { exact: true }).fill('3');
      if (tag === 'B') {
        await form.getByLabel('Разрешить бессрочный резерв', { exact: true }).check();
        await form.getByLabel('Срок по умолчанию, часов', { exact: true }).fill('');
      }
      return mutation(ap, '/number-series', () => form.getByRole('button', { name: 'Сохранить серию', exact: true }).click(), 'POST', 201);
    }
    const seriesA = await createSeriesUI('A', 10, 2);
    const seriesB = await createSeriesUI('B', 1, 1);
    pass('administrator creates two independent synthetic series in UI with formats and policies');

    const grantValues = [
      { account: administrator, permissions: ['view', 'reserve', 'assign', 'cancel', 'void'] },
      { account: roles.editor1.account, permissions: ['view', 'reserve', 'assign', 'cancel'] },
      { account: roles.editor2.account, permissions: ['view', 'reserve', 'assign', 'cancel'] },
      { account: roles.reader.account, permissions: ['view'] },
    ];
    for (const series of [seriesA, seriesB]) {
      await ap.getByRole('combobox', { name: 'Серия номеров', exact: true }).selectOption(series.id);
      const grants = ap.locator('details').filter({ has: ap.getByText('Права на серию', { exact: true }) });
      await grants.locator('summary').click();
      for (const value of grantValues) {
        await grants.getByRole('combobox', { name: 'Пользователь серии', exact: true }).selectOption(value.account.id);
        await grants.getByRole('button', { name: 'Добавить пользователя серии', exact: true }).click();
        const choices = grants.getByRole('group', { name: value.account.display_name, exact: true });
        for (const [permission, label] of [['view', 'Просмотр'], ['reserve', 'Резервирование'], ['assign', 'Присвоение'], ['cancel', 'Отмена резерва'], ['void', 'Погашение']]) {
          await choices.getByLabel(label, { exact: true }).setChecked(value.permissions.includes(permission));
        }
      }
      await mutation(ap, `/number-series/${series.id}/grants`, () => grants.getByRole('button', { name: 'Сохранить права серии', exact: true }).click(), 'PUT');
    }
    pass('administrator grants series view reserve assign cancel and void permissions through UI');

    const versions = await json(ap, `/document-types/${type.id}/versions`);
    const version = versions[0];
    const schema = { sections: [{ key: 'main', title: 'Основные сведения', fields: [
      { key: 'number', label: 'Номер Task004', type: 'text', system: 'reg_number', number_series_id: seriesA.id, required: true, editable: true, filterable: true, exportable: true },
      { key: 'title', label: 'Название Task004', type: 'text', system: 'title', required: true, editable: true, exportable: true },
    ] }] };
    await json(ap, `/form-schemas/${version.id}`, 'PUT', { schema });
    await json(ap, `/form-schemas/${version.id}/publish`, 'POST', {});
    for (const value of Object.values(roles)) await value.page.reload();
    await json(ep, '/documents', 'POST', { registry_id: registry.id, document_type_id: type.id, schema_version_id: version.id, values: { number: 'MANUAL-NOT-ISSUED', title: title + ' denied' } }, 403);
    pass('bound registration field rejects arbitrary manual number through direct API');

    const firstKey = key('parallel1');
    const firstBody = { count: 1, idempotency_key: firstKey };
    const [firstResult, secondResult] = await Promise.all([
      json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', firstBody),
      json(second, `/number-series/${seriesA.id}/reserve`, 'POST', { count: 1, idempotency_key: key('parallel2') }),
    ]);
    const firstEntry = firstResult.items[0];
    const secondEntry = secondResult.items[0];
    assert.deepEqual([firstEntry.ordinal, secondEntry.ordinal].sort((a, b) => a - b), [10, 12]);
    assert.notEqual(firstEntry.number, secondEntry.number);
    const beforeRetry = await json(ep, `/number-series/${seriesA.id}/stats`);
    const retried = await json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', firstBody);
    assert.equal(retried.items[0].id, firstEntry.id);
    await json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', { ...firstBody, count: 2 }, 409);
    assert.equal((await json(ep, `/number-series/${seriesA.id}/stats`)).next_ordinal, beforeRetry.next_ordinal);
    const batch = await json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', { count: 3, idempotency_key: key('batch') });
    assert.deepEqual(batch.items.map(entry => entry.ordinal), [14, 16, 18]);
    const beforeRejected = await json(ep, `/number-series/${seriesA.id}/stats`);
    await json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', { count: 4, idempotency_key: key('oversized') }, 422);
    assert.equal((await json(ep, `/number-series/${seriesA.id}/stats`)).next_ordinal, beforeRejected.next_ordinal);
    const independent = await json(ep, `/number-series/${seriesB.id}/reserve`, 'POST', { count: 1, idempotency_key: key('independent') });
    assert.equal(independent.items[0].ordinal, 1);
    assert.equal(independent.items[0].number, 'B-2026/0001');
    assert.equal(independent.items[0].expires_at, null);
    await json(ep, `/number-entries/${independent.items[0].id}/expire`, 'POST', { reason: 'Cannot expire indefinite reserve', idempotency_key: key('indefiniteexpire') }, 409);
    pass('two editors concurrently reserve unique numbers bounded batch and idempotent retry');

    await ep.getByRole('navigation').getByRole('button', { name: 'Реестр номеров', exact: true }).click();
    await ep.getByRole('combobox', { name: 'Серия номеров', exact: true }).selectOption(seriesA.id);
    await ep.getByLabel('Количество номеров', { exact: true }).fill('1');
    const uiReserve = await mutation(ep, `/number-series/${seriesA.id}/reserve`, () => ep.getByRole('button', { name: 'Зарезервировать', exact: true }).click());
    const uiEntry = uiReserve.items[0];
    const uiRetry = await mutation(ep, `/number-series/${seriesA.id}/reserve`, () => ep.getByRole('button', { name: 'Повторить запрос резерва', exact: true }).click());
    assert.equal(uiRetry.items[0].id, uiEntry.id);
    const cancelRow = ep.getByRole('row').filter({ has: ep.getByText(uiEntry.number, { exact: true }) });
    await cancelRow.getByRole('button', { name: 'Отменить резерв', exact: true }).click();
    await cancelRow.getByLabel('Причина операции', { exact: true }).fill('Synthetic Task004 cancellation');
    const cancelled = await mutation(ep, `/number-entries/${uiEntry.id}/cancel`, () => cancelRow.getByRole('button', { name: 'Подтвердить операцию', exact: true }).click());
    assert.equal(cancelled.state, 'cancelled');
    const afterCancel = await json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', { count: 1, idempotency_key: key('aftercancel') });
    assert.ok(afterCancel.items[0].ordinal > uiEntry.ordinal);
    assert.notEqual(afterCancel.items[0].number, uiEntry.number);
    pass('editor UI reserve retry cancel with reason and cancelled number never reused');
    const intervalsPanel = ep.locator('details').filter({ has: ep.getByText('Занятые и свободные интервалы', { exact: true }) });
    await intervalsPanel.locator('summary').click();
    await intervalsPanel.getByLabel('Период интервалов', { exact: true }).fill('2026');
    await intervalsPanel.getByLabel('Начиная с', { exact: true }).fill('10');
    await intervalsPanel.getByLabel('До значения', { exact: true }).fill('26');
    const intervals = await mutation(ep, `/number-series/${seriesA.id}/intervals`, () => intervalsPanel.getByRole('button', { name: 'Найти интервалы', exact: true }).click(), 'GET');
    assert.ok(intervals.intervals.some(value => value.state === 'free'));
    assert.ok(intervals.intervals.some(value => value.state === 'cancelled' && value.from_ordinal <= uiEntry.ordinal && value.to_ordinal >= uiEntry.ordinal));
    await intervalsPanel.getByText('Свободно', { exact: true }).first().waitFor();
    pass('editor searches bounded occupied cancelled and free intervals in UI');

    await ep.getByRole('navigation').getByRole('button', { name: 'Мои реестры', exact: true }).click();
    await ep.getByRole('button', { name: title, exact: true }).click();
    await ep.getByRole('button', { name: 'Создать документ', exact: true }).click();
    await ep.getByRole('combobox', { name: 'Тип документа', exact: true }).selectOption(type.id);
    await ep.getByLabel('Название Task004', { exact: false }).fill(`${title} assigned card`);
    const card = await mutation(ep, '/documents', () => ep.getByRole('button', { name: 'Создать документ', exact: true }).click(), 'POST', 201);
    assert.equal(card.reg_number || '', '');
    await json(ep, `/number-entries/${independent.items[0].id}/assign`, 'POST', { document_id: card.id, revision: card.revision, idempotency_key: key('wrongseries') }, 403);
    await ep.getByRole('combobox', { name: 'Резерв для карточки', exact: true }).selectOption(firstEntry.id);
    const assigned = await mutation(ep, `/number-entries/${firstEntry.id}/assign`, () => ep.getByRole('button', { name: 'Присвоить номер карточке', exact: true }).click());
    assert.equal(assigned.state, 'assigned');
    assert.equal(assigned.document_id, card.id);
    const currentCard = await json(ep, `/documents/${card.id}`);
    assert.equal(currentCard.reg_number, firstEntry.number);
    assert.ok(currentCard.revision > card.revision);
    await json(ep, `/documents/${card.id}`, 'PUT', { revision: currentCard.revision, values: { number: 'ARBITRARY-REPLACE', title: `${title} assigned card` } }, 403);
    await json(second, `/number-entries/${uiEntry.id}/assign`, 'POST', { document_id: card.id, revision: currentCard.revision, idempotency_key: key('cancelledassign') }, 409);
    pass('editor UI assigns reserved number atomically to generated card and manual replacement denied');

    await vp.getByRole('navigation').getByRole('button', { name: 'Реестр номеров', exact: true }).click();
    await vp.getByRole('combobox', { name: 'Серия номеров', exact: true }).selectOption(seriesA.id);
    await vp.getByRole('row').filter({ has: vp.getByText(firstEntry.number, { exact: true }) }).getByText('Присвоен', { exact: true }).waitFor();
    assert.equal(await vp.getByRole('button', { name: 'Зарезервировать', exact: true }).count(), 0);
    assert.equal(await vp.getByRole('button', { name: 'Присвоить номер', exact: true }).count(), 0);
    await json(vp, `/number-series/${seriesA.id}/reserve`, 'POST', { count: 1, idempotency_key: key('denyreserve') }, 403);
    await json(vp, `/number-entries/${firstEntry.id}/cancel`, 'POST', { reason: 'Reader forbidden', idempotency_key: key('denytransition') }, 403);
    await json(vp, `/number-entries/${batch.items[0].id}/assign`, 'POST', { document_id: card.id, revision: currentCard.revision, idempotency_key: key('denyassign') }, 403);
    await json(ep, '/number-series', 'POST', { code: code + '_denied', name: title, registry_id: registry.id }, 403);
    await json(vp, `/number-series/${seriesA.id}/grants`, 'PUT', { entries: [] }, 403);
    const publicRows = await json(vp, `/number-series/${seriesA.id}/entries`);
    assert.ok(publicRows.items.every(entry => !entry.owner_id));
    const readerHistory = await json(vp, `/number-entries/${uiEntry.id}/history`);
    assert.ok(readerHistory.items.every(value => !value.actor_id && !value.reason));
    await json(vp, `/number-series/${seriesA.id}/intervals?from_ordinal=10&to_ordinal=26`, 'GET', undefined, 403);
    pass('reader journal read only and direct reserve assign cancel grants administration denied');

    await json(ap, `/documents/${card.id}/acl`, 'PUT', { entries: [
      { user_id: administrator.id, permissions: ['read', 'update', 'download'] },
      { user_id: roles.editor1.account.id, permissions: ['read', 'update', 'download'] },
    ] });
    const redacted = await json(vp, `/number-series/${seriesA.id}/entries`);
    assert.equal(redacted.items.some(entry => entry.id === firstEntry.id), false);
    assert.equal(JSON.stringify(redacted).includes(card.id), false);
    assert.equal(JSON.stringify(redacted).includes(`${title} assigned card`), false);
    await json(vp, `/number-entries/${firstEntry.id}/history`, 'GET', undefined, 404);
    await vp.getByRole('button', { name: 'Обновить журнал', exact: true }).click();
    await vp.getByRole('row').filter({ has: vp.getByText(firstEntry.number, { exact: true }) }).waitFor({ state: 'detached' });
    await vp.getByLabel('Период журнала', { exact: true }).fill('2026');
    await vp.getByRole('button', { name: 'Найти номера', exact: true }).click();
    const csvPath = await download(vp, 'Экспорт номеров CSV', 'number-register-reader.csv');
    await download(vp, 'Экспорт номеров XLSX', 'number-register-reader.xlsx');
    const csv = await fs.readFile(csvPath, 'utf8');
    assert.equal(csv.includes(firstEntry.number), false);
    assert.equal(csv.includes(card.id), false);
    assert.ok(csv.includes(uiEntry.number));
    for (const id of accountIds) assert.equal(csv.includes(id), false);
    await vp.screenshot({ path: path.join(outputDir, 'reader-number-journal.png'), fullPage: true });
    pass('document ACL removes restricted number from reader UI API history CSV and XLSX');
    await vp.setViewportSize({ width: 390, height: 844 });
    assert.ok(await vp.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await vp.screenshot({ path: path.join(outputDir, 'reader-number-journal-mobile.png'), fullPage: true });
    await vp.setViewportSize({ width: 1440, height: 1000 });
    pass('reader number journal fits mobile viewport');

    await ap.getByRole('combobox', { name: 'Серия номеров', exact: true }).selectOption(seriesA.id);
    await ap.getByRole('button', { name: 'Настроить серию', exact: true }).click();
    const settings = ap.locator('form').filter({ has: ap.getByRole('heading', { name: 'Настройки серии' }) });
    await settings.getByRole('combobox', { name: 'Состояние серии', exact: true }).selectOption('blocked');
    await mutation(ap, `/number-series/${seriesA.id}`, () => settings.getByRole('button', { name: 'Сохранить серию', exact: true }).click(), 'PATCH');
    await json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', { count: 1, idempotency_key: key('blocked') }, 409);
    await json(ap, `/number-series/${seriesA.id}`, 'PATCH', { state: 'active' });
    await ap.reload();
    await ap.getByRole('navigation').getByRole('button', { name: 'Серии номеров', exact: true }).click();
    await ap.getByRole('combobox', { name: 'Серия номеров', exact: true }).selectOption(seriesA.id);
    await ap.getByRole('button', { name: 'Настроить серию', exact: true }).click();
    await settings.getByLabel('Текущий период', { exact: true }).fill('2027');
    await mutation(ap, `/number-series/${seriesA.id}`, () => settings.getByRole('button', { name: 'Сохранить серию', exact: true }).click(), 'PATCH');
    const nextPeriod = await json(ep, `/number-series/${seriesA.id}/reserve`, 'POST', { count: 1, idempotency_key: key('periodtransition') });
    assert.equal(nextPeriod.items[0].period_key, '2027');
    assert.equal(nextPeriod.items[0].ordinal, 10);
    assert.equal(nextPeriod.items[0].number, 'A-2027/0010');
    assert.notEqual(nextPeriod.items[0].number, firstEntry.number);
    const previousPeriod = await json(ep, `/number-series/${seriesA.id}/entries?period_key=2026`);
    assert.ok(previousPeriod.items.some(entry => entry.id === firstEntry.id));
    pass('administrator blocks allocation and transitions real synthetic period retaining old journal');

    await json(ap, `/number-entries/${firstEntry.id}/void`, 'POST', { reason: 'Synthetic Task004 void', idempotency_key: key('void') });
    assert.equal((await json(ep, `/documents/${card.id}`)).reg_number, firstEntry.number);
    const voidRows = await json(ap, `/number-series/${seriesA.id}/entries?state=void`);
    assert.ok(voidRows.items.some(entry => entry.id === firstEntry.id));
    pass('void retains immutable number and original card registration string');

    const types = await json(ap, '/document-types');
    const legacyType = types.find(value => value.code === 'acti-v2');
    assert.ok(legacyType);
    const registries = await json(ap, '/registries');
    const legacyRegistry = registries.find(value => value.document_type_ids.includes(legacyType.id));
    assert.ok(legacyRegistry);
    const legacyGrants = await json(ap, `/admin/access/${legacyRegistry.id}`);
    await json(ap, `/admin/access/${legacyRegistry.id}`, 'PUT', { entries: [...legacyGrants.entries, ...Object.values(roles).map(value => ({ user_id: value.account.id, permissions: value.permissions }))] });
    temporaryRegistryGrants.add(legacyRegistry.id);
    await ep.reload();
    await ep.getByRole('button', { name: legacyRegistry.name, exact: true }).click();
    await ep.getByRole('button', { name: 'Создать документ', exact: true }).click();
    await ep.getByRole('combobox', { name: 'Тип документа', exact: true }).selectOption(legacyType.id);
    await ep.getByLabel('Наименование документа', { exact: false }).fill(`${title} synthetic ACTI v2`);
    await ep.getByLabel('Регистрационный номер', { exact: true }).fill('000004');
    await ep.getByRole('combobox', { name: 'Тип документа ACTI v2', exact: true }).selectOption({ label: 'Постановление' });
    await ep.getByLabel('Номер тома', { exact: true }).fill('1-2');
    await ep.getByLabel('Листы', { exact: true }).fill('001-010');
    const legacyCard = await mutation(ep, '/documents', () => ep.getByRole('button', { name: 'Создать документ', exact: true }).click(), 'POST', 201);
    assert.equal(legacyCard.reg_number, '000004');
    assert.equal(legacyCard.values.should_publish ?? null, null);
    assert.equal(legacyCard.values.volume_number, '1-2');
    await vp.reload();
    await vp.getByRole('button', { name: legacyRegistry.name, exact: true }).click();
    await vp.getByLabel('Поиск', { exact: true }).fill(`${title} synthetic ACTI v2`);
    await vp.getByRole('button', { name: 'Найти', exact: true }).click();
    await vp.getByText('Найдено: 1', { exact: true }).waitFor();
    await vp.getByRole('row', { name: /Выбрать документ/ }).click();
    await vp.getByText('Постановление', { exact: true }).first().waitFor();
    await vp.getByText('000004', { exact: true }).first().waitFor();
    await vp.screenshot({ path: path.join(outputDir, 'acti-v2-unbound-legacy-number.png'), fullPage: true });
    pass('ACTI v2 unbound historical style number nullable publication references and volume strings remain compatible');

    for (const page of [ep, second, vp]) {
      await page.goto(`${origin}/admin`);
      await page.waitForURL(url => url.pathname !== '/admin');
      await page.getByRole('button', { name: 'Выйти', exact: true }).click();
      await page.getByRole('heading', { name: 'Вход в ACTI', exact: true }).waitFor();
      await json(page, '/users/me', 'GET', undefined, 401);
    }
    assert.deepEqual(h.runtimeErrors, [], 'No uncaught application JavaScript errors');
    pass('both editors reader cannot open administration and real logout revokes sessions');
    const durable = await json(ap, `/number-series/${seriesA.id}/entries?page_size=100`);
    const stats = await json(ap, `/number-series/${seriesA.id}/stats`);
    const persistence = { source_sha: platform.source_sha, series_id: seriesA.id, period_key: stats.period_key, next_ordinal: stats.next_ordinal, next_number: stats.next_number, entries: durable.items.map(entry => ({ id: entry.id, ordinal: entry.ordinal, number: entry.number, state: entry.state, period_key: entry.period_key, document_id: entry.document_id })), document_id: card.id, document_number: firstEntry.number };
    await fs.writeFile(path.join(outputDir, 'persistence.json'), JSON.stringify(persistence, null, 2));
    return { status: 'PASS', source_sha: platform.source_sha, scenarios: h.results, persistence: 'PENDING_GUARDED_RESTART', synthetic: { series_ids: [seriesA.id, seriesB.id], registry_id: registry.id, document_id: card.id, legacy_document_id: legacyCard.id } };
  } catch (error) {
    await fs.writeFile(path.join(outputDir, 'failure.txt'), sensitive.reduce((message, value) => message.split(value).join('[redacted]'), String(error.message)));
    await ap?.screenshot({ path: path.join(outputDir, 'failure-admin.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    const failures = [];
    if (ap) for (const registryId of temporaryRegistryGrants) {
      try {
        const current = await json(ap, `/admin/access/${registryId}`);
        await json(ap, `/admin/access/${registryId}`, 'PUT', { entries: current.entries.filter(value => !accountIds.includes(value.user_id)) });
        const restored = await json(ap, `/admin/access/${registryId}`);
        assert.equal(restored.entries.some(value => accountIds.includes(value.user_id)), false);
      } catch { failures.push('temporary registry grant'); }
    }
    if (ap) for (const id of accountIds) {
      try { await json(ap, `/admin/users/${id}`, 'PATCH', { active: false }); }
      catch { failures.push('synthetic account'); }
    }
    if (ap && accountIds.length) {
      try {
        const users = await json(ap, '/admin/users');
        assert.ok(accountIds.every(id => users.some(user => user.id === id && user.active === false)));
      } catch { failures.push('synthetic account verification'); }
    }
    for (const context of h.contexts) await context.close();
    await fs.writeFile(path.join(outputDir, 'scenarios.json'), JSON.stringify(h.results, null, 2));
    assert.deepEqual(failures, [], 'Synthetic cleanup must succeed');
  }
}
export async function verifyTask004Persistence({ browser, origin, admin, outputDir }) {
  const h = await helpers(browser, origin, outputDir);
  try {
    const previous = JSON.parse(await fs.readFile(path.join(outputDir, 'persistence.json'), 'utf8'));
    const page = await h.pageFor();
    await h.login(page, admin);
    const platform = await h.json(page, '/admin/platform');
    assert.equal(platform.source_sha, previous.source_sha, 'The same committed release must survive guarded restart');
    const entries = await h.json(page, `/number-series/${previous.series_id}/entries?page_size=100`);
    for (const wanted of previous.entries) {
      const found = entries.items.find(entry => entry.id === wanted.id);
      assert.ok(found, 'Existing number identity preserved');
      for (const [key, value] of Object.entries(wanted)) assert.equal(found[key], value, `Persisted number ${key}`);
    }
    const document = await h.json(page, `/documents/${previous.document_id}`);
    assert.equal(document.reg_number, previous.document_number);
    const stats = await h.json(page, `/number-series/${previous.series_id}/stats`);
    assert.equal(stats.period_key, previous.period_key);
    assert.equal(stats.next_ordinal, previous.next_ordinal);
    assert.equal(stats.next_number, previous.next_number);
    const next = await h.json(page, `/number-series/${previous.series_id}/reserve`, 'POST', { count: 1, idempotency_key: key('afterrestart') });
    assert.equal(next.items[0].ordinal, previous.next_ordinal);
    assert.equal(next.items[0].number, previous.next_number);
    assert.equal(next.items[0].period_key, previous.period_key);
    await page.getByRole('navigation').getByRole('button', { name: 'Серии номеров', exact: true }).click();
    await page.getByRole('combobox', { name: 'Серия номеров', exact: true }).selectOption(previous.series_id);
    await page.getByRole('row').filter({ has: page.getByText(next.items[0].number, { exact: true }) }).waitFor();
    await page.screenshot({ path: path.join(outputDir, 'number-register-after-restart.png'), fullPage: true });
    h.pass('guarded restart preserves identities numbers states periods document link and next allocation');
    const receipt = { status: 'PASS', source_sha: platform.source_sha, scenarios: h.results, preserved_entries: previous.entries.length, next_ordinal: next.items[0].ordinal };
    await fs.writeFile(path.join(outputDir, 'persistence-acceptance.json'), JSON.stringify(receipt, null, 2));
    return receipt;
  } finally { for (const context of h.contexts) await context.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  let browser;
  try {
    const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
    const admin = JSON.parse(await fs.readFile(config.adminCredentialsFile, 'utf8'));
    const { chromium } = await import(config.playwrightModule ? pathToFileURL(config.playwrightModule).href : 'playwright');
    browser = await chromium.launch({ headless: true, ...(config.executablePath ? { executablePath: config.executablePath } : {}) });
    const checkPersistence = process.argv.includes('--verify-persistence');
    const run = checkPersistence ? verifyTask004Persistence : runTask004;
    const receipt = await run({ browser, origin: config.origin, admin, outputDir: config.outputDir });
    if (!checkPersistence) await fs.writeFile(path.join(config.outputDir, 'acceptance.json'), JSON.stringify(receipt, null, 2));
    console.log(`Task004 actual HTTPS browser ${checkPersistence ? 'persistence' : 'functional'} PASS: ${receipt.scenarios.length} scenarios.`);
  } catch (error) {
    console.error(`Task004 browser acceptance FAIL: ${error.name}: ${error.message.split('\n')[0]}`);
    process.exitCode = 1;
  } finally { await browser?.close(); }
}
