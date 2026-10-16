/** Genuine Task011 HTTPS/OIDC acceptance. No response mocks, TLS bypass or stored cookies.
 * Config: origin, adminCredentialsFile, idpCredentialsFile, receiptFile, outputDir,
 * optional playwrightModule/executablePath/domainCredentialsFile/expectedSourceSha.
 * Domain credentials accept login (or reader_sam), password and optional subject.
 * A domain subject is linked temporarily only to this run's owned reader account.
 * The receipt contains
 * generated local credentials and must live in the operator's protected secret directory.
 * Run phases setup -> verify (repeat after restart) -> cleanup. No existing user is linked.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {randomBytes, randomUUID} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const FORMAT = 'acti-task011-oidc-owned-pilot-v1';
const ROLES = ['admin', 'editor', 'viewer'];
const uuid = value => typeof value === 'string' && /^[0-9a-f-]{36}$/i.test(value);
const password = () => randomBytes(32).toString('base64url');

async function privateWrite(filename, value) {
  const parent = await fs.realpath(path.dirname(filename));
  assert.equal(path.resolve(parent), path.resolve(path.dirname(filename)), 'Private receipt parent is a real directory');
  if (process.platform !== 'win32') {
    const stat = await fs.stat(parent);
    assert.equal(stat.mode & 0o077, 0, 'Private receipt parent permissions');
  }
  const temporary = filename + '.' + randomUUID() + '.partial';
  const handle = await fs.open(temporary, 'wx', 0o600);
  try { await handle.writeFile(JSON.stringify(value, null, 2) + '\n'); await handle.sync(); }
  finally { await handle.close(); }
  await fs.rename(temporary, filename);
}

async function readReceipt(filename, origin) {
  const info = await fs.lstat(filename);
  assert.ok(info.isFile() && !info.isSymbolicLink(), 'Private receipt is a regular file');
  if (process.platform !== 'win32') assert.equal(info.mode & 0o077, 0, 'Private receipt permissions');
  const receipt = JSON.parse(await fs.readFile(filename, 'utf8'));
  assert.equal(receipt.format, FORMAT);
  assert.equal(receipt.origin, origin);
  assert.ok(uuid(receipt.run_id));
  assert.equal(receipt.prefix, 'task011_oidc_' + receipt.run_id.replaceAll('-', ''));
  assert.ok(Array.isArray(receipt.accounts) && receipt.accounts.length === 3);
  for (const [index, account] of receipt.accounts.entries()) {
    assert.equal(account.role, ROLES[index]);
    assert.equal(account.login, receipt.prefix + '_' + account.role);
    assert.equal(account.display_name, 'Task011 OIDC ' + account.role + ' ' + receipt.run_id);
    if (account.id) assert.ok(uuid(account.id));
    if (account.identity_id) assert.ok(uuid(account.identity_id));
  }
  if (receipt.registry) {
    assert.ok(uuid(receipt.registry.id));
    assert.equal(receipt.registry.code, receipt.prefix);
  }
  return receipt;
}

export async function runTask011Oidc({browser, origin, admin, idp, outputDir, receiptFile, phase, domain, expectedSourceSha}) {
  const target = new URL(origin);
  assert.equal(target.protocol, 'https:');
  assert.equal(target.origin, origin);
  assert.ok(['setup', 'verify', 'cleanup'].includes(phase));
  assert.ok(receiptFile && path.isAbsolute(receiptFile));
  assert.ok(Array.isArray(idp.pilot_accounts));
  const providers = new Map(idp.pilot_accounts.map(account => [account.role, account]));
  for (const role of [...ROLES, 'unlinked']) {
    const account = providers.get(role);
    assert.ok(account?.login && account?.password && account?.subject, 'Provider pilot exists');
  }
  await fs.mkdir(outputDir, {recursive: true, mode: 0o700});
  const pages = [], runtimeErrors = [], results = [];
  let stage = 'initialize', receipt, summary, lastAuthentication = Date.now();
  const pass = name => { results.push({name, status: 'PASS'}); console.log('PASS ' + name); };
  const save = () => privateWrite(receiptFile, receipt);
  async function pageFor() {
    const context = await browser.newContext({ignoreHTTPSErrors: false, viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    page.on('pageerror', () => runtimeErrors.push('javascript_runtime_error'));
    pages.push(page);
    return page;
  }
  async function json(page, endpoint, method = 'GET', data, expected = 200) {
    const response = await page.evaluate(async ({endpoint, method, data}) => {
      let token;
      if (method !== 'GET') {
        const me = await fetch('/api/v1/users/me', {credentials: 'same-origin'});
        if (me.ok) token = (await me.json()).csrf_token;
      }
      const response = await fetch('/api/v1' + endpoint, {
        method, credentials: 'same-origin',
        headers: {Accept: 'application/json', ...(token ? {'X-CSRF-Token': token} : {}),
          ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
        ...(data === undefined ? {} : {body: JSON.stringify(data)}),
      });
      return {status: response.status, body: await response.json().catch(() => null)};
    }, {endpoint, method, data});
    assert.equal(response.status, expected, 'Expected API authorization status');
    return response.body;
  }
  async function paceAuthentication() {
    await delay(Math.max(0, 6500 - (Date.now() - lastAuthentication)));
    lastAuthentication = Date.now();
  }
  async function localLogin(page, credentials) {
    stage = 'local authentication';
    await paceAuthentication();
    await page.goto(origin);
    await page.getByLabel('Логин', {exact: true}).fill(credentials.login);
    await page.getByLabel('Пароль', {exact: true}).fill(credentials.password);
    await page.getByRole('button', {name: 'Войти в рабочее пространство'}).click();
    await page.getByRole('button', {name: 'Выйти', exact: true}).waitFor();
  }
  async function providerLogin(credentials, denied = false) {
    stage = denied ? 'unlinked provider authentication' : 'linked provider authentication';
    await paceAuthentication();
    const page = await pageFor();
    await page.goto(origin);
    await page.getByRole('heading', {name: 'Вход в ACTI', exact: true}).waitFor();
    await page.getByRole('button', {name: /^Войти через /}).click();
    await page.locator('#username').waitFor();
    const url = new URL(page.url());
    assert.equal(url.origin, origin, 'Provider stays on the verified HTTPS origin');
    assert.ok(url.pathname.startsWith('/idp/realms/acti/'), 'Genuine configured provider realm');
    await page.locator('#username').fill(credentials.login);
    await page.locator('#password').fill(credentials.password);
    const [response] = await Promise.all([
      page.waitForResponse(response => {
        const address = new URL(response.url());
        return address.origin === origin && address.pathname === '/api/v1/auth/oidc/callback';
      }),
      page.locator('#kc-login').click(),
    ]);
    assert.equal(response.status(), 302, 'Provider callback completed');
    const location = new URL(await response.headerValue('location'));
    assert.equal(location.origin, origin);
    if (denied) {
      assert.equal(location.searchParams.get('auth_error'), 'identity_not_linked');
      await page.getByRole('alert').filter({hasText: 'Не удалось выполнить единый вход'}).waitFor();
      await page.waitForURL(value => value.origin === origin && !value.searchParams.has('auth_error'));
      assert.equal(new URL(page.url()).searchParams.has('auth_error'), false);
      await json(page, '/users/me', 'GET', undefined, 401);
    } else {
      assert.equal(location.pathname, '/');
      assert.equal(location.search, '');
      await page.getByRole('button', {name: 'Выйти', exact: true}).waitFor();
      await page.getByRole('heading', {name: /^(Обзор системы|Мои реестры)$/}).waitFor();
      const cookies = await page.context().cookies(origin);
      const session = cookies.find(cookie => cookie.name === 'acti_session');
      assert.ok(session?.secure && session.httpOnly && session.sameSite === 'Lax', 'Secure server session cookie');
    }
    return page;
  }
  async function ownedUser(page, account, optional = false) {
    const users = await json(page, '/admin/users');
    const user = users.find(value => account.id ? value.id === account.id : value.login === account.login);
    if (!user && optional && !account.id) return null;
    assert.ok(user, 'Owned synthetic user exists');
    assert.equal(user.login, account.login);
    assert.equal(user.display_name, account.display_name);
    assert.equal(user.kind, 'local');
    return user;
  }
  async function link(page, account) {
    const identity = await json(page, `/admin/users/${account.id}/external-identities`, 'POST', {subject: providers.get(account.role).subject});
    assert.equal(identity.subject, providers.get(account.role).subject);
    assert.equal(identity.active, true);
    if (account.identity_id) assert.equal(identity.id, account.identity_id);
    account.identity_id = identity.id;
    await save();
  }
  try {
    if (phase === 'setup') {
      assert.equal(await fs.stat(receiptFile).then(() => true, () => false), false, 'Setup never overwrites an existing private receipt');
      const runId = randomUUID(), prefix = 'task011_oidc_' + runId.replaceAll('-', '');
      receipt = {format: FORMAT, run_id: runId, prefix, origin, state: 'initializing', accounts: ROLES.map(role => ({
        role, login: prefix + '_' + role, display_name: 'Task011 OIDC ' + role + ' ' + runId,
        temporary_password: password(), password: password(), password_changed: false,
      }))};
      await save();
    } else receipt = await readReceipt(receiptFile, origin);
    const emergency = await pageFor();
    await localLogin(emergency, admin);
    const me = await json(emergency, '/users/me');
    assert.ok(me.roles.includes('admin'));
    if (receipt.emergency_admin_id) assert.equal(me.id, receipt.emergency_admin_id);
    receipt.emergency_admin_id = me.id;
    const platform = await json(emergency, '/admin/platform');
    assert.ok(/^[0-9a-f]{40}$/.test(platform.source_sha));
    if (expectedSourceSha) assert.equal(platform.source_sha, expectedSourceSha);
    receipt.last_source_sha = platform.source_sha;
    await save();
    pass('local emergency administrator HTTPS login');

    if (phase === 'setup') {
      stage = 'create owned synthetic users';
      for (const account of receipt.accounts) {
        const user = await json(emergency, '/admin/users', 'POST', {
          login: account.login, display_name: account.display_name,
          password: account.temporary_password, roles: [account.role],
        }, 201);
        account.id = user.id;
        await save();
        const page = await pageFor();
        await localLogin(page, {login: account.login, password: account.temporary_password});
        await page.getByLabel('Текущий пароль', {exact: true}).fill(account.temporary_password);
        await page.getByLabel('Новый пароль', {exact: true}).fill(account.password);
        await page.getByLabel('Повторите новый пароль', {exact: true}).fill(account.password);
        await page.getByRole('button', {name: 'Изменить пароль', exact: true}).click();
        await page.getByRole('heading', {
          name: account.role === 'admin' ? 'Обзор системы' : 'Мои реестры', exact: true,
        }).waitFor();
        const current = await json(page, '/users/me');
        assert.equal(current.id, account.id);
        assert.equal(current.must_change_password, false);
        account.password_changed = true;
        delete account.temporary_password;
        await save();
        await json(page, '/auth/logout', 'POST', undefined, 204);
        await link(emergency, account);
      }
      const registry = await json(emergency, '/registries', 'POST', {
        code: receipt.prefix, name: 'Task011 OIDC synthetic access proof', description: receipt.run_id,
        document_type_ids: [],
      }, 201);
      receipt.registry = registry;
      await save();
      await json(emergency, `/admin/access/${registry.id}`, 'PUT', {entries: [
        {user_id: me.id, permissions: ['read']},
        ...receipt.accounts.map(account => ({user_id: account.id, permissions: ['read']})),
      ]});
      receipt.state = 'ready';
      await save();
      pass('three owned users changed initial passwords and explicitly linked immutable provider subjects');
    } else if (phase === 'verify') {
      assert.equal(receipt.state, 'ready');
      for (const account of receipt.accounts) {
        const user = await ownedUser(emergency, account);
        assert.equal(user.active, true);
        assert.deepEqual(user.roles, [account.role]);
        assert.equal(user.must_change_password, false);
        const identities = await json(emergency, `/admin/users/${account.id}/external-identities`);
        assert.ok(identities.some(value => value.id === account.identity_id && value.active && value.subject === providers.get(account.role).subject));
      }
      const sessions = new Map();
      for (const account of receipt.accounts) {
        const page = await providerLogin(providers.get(account.role));
        const current = await json(page, '/users/me');
        assert.equal(current.id, account.id, 'External login preserves ACTI UUID');
        assert.deepEqual(current.roles, [account.role], 'ACTI roles remain authoritative');
        if (account.role === 'admin') await json(page, '/admin/users');
        else await json(page, '/admin/users', 'GET', undefined, 403);
        const registries = await json(page, '/registries');
        assert.ok(registries.some(value => value.id === receipt.registry.id));
        sessions.set(account.role, page);
      }
      pass('real provider login preserves UUID and local roles for administrator editor and reader');
      const viewer = sessions.get('viewer');
      await json(viewer, `/registries/${receipt.registry.id}`, 'PATCH', {name: 'Forbidden viewer mutation'}, 403);
      const registries = await json(emergency, '/registries');
      assert.equal(registries.find(value => value.id === receipt.registry.id)?.name, receipt.registry.name);
      await viewer.screenshot({path: path.join(outputDir, 'reader-own-fixture.png'), fullPage: false});
      pass('editor reader administration and reader registry mutation are denied');
      await providerLogin(providers.get('unlinked'), true);
      pass('unlinked provider user cannot obtain an ACTI session');
      if (domain) {
        assert.ok(domain.login && domain.password);
        const reader = receipt.accounts.find(value => value.role === 'viewer');
        const domainLinks = await json(emergency, `/admin/users/${reader.id}/external-identities`);
        const existingDomainLink = domain.subject ? domainLinks.find(value => value.subject === domain.subject && value.active) : null;
        if (existingDomainLink) {
          assert.equal(receipt.domain_identity.user_id, reader.id);
          assert.equal(receipt.domain_identity.subject, domain.subject);
          if (receipt.domain_identity.id) assert.equal(existingDomainLink.id, receipt.domain_identity.id);
          await json(emergency, `/admin/users/${reader.id}/external-identities/${existingDomainLink.id}`, 'DELETE', undefined, 204);
          receipt.domain_identity.id = existingDomainLink.id;
          receipt.domain_identity.active = false;
          await save();
        }
        await providerLogin(domain, true);
        pass('real domain password verified by provider with unlinked ACTI access denied');
        if (domain.subject) {
          receipt.domain_identity = {user_id: reader.id, subject: domain.subject, active: false};
          await save();
          const identity = await json(emergency, `/admin/users/${reader.id}/external-identities`, 'POST', {subject: domain.subject});
          assert.equal(identity.subject, domain.subject);
          assert.notEqual(identity.id, reader.identity_id);
          receipt.domain_identity = {id: identity.id, user_id: reader.id, subject: domain.subject, active: true};
          await save();
          let domainPage;
          try {
            domainPage = await providerLogin(domain);
            const current = await json(domainPage, '/users/me');
            assert.equal(current.id, reader.id);
            assert.deepEqual(current.roles, ['viewer']);
            await json(domainPage, '/admin/users', 'GET', undefined, 403);
            await json(domainPage, `/registries/${receipt.registry.id}`, 'PATCH', {name: 'Forbidden domain reader mutation'}, 403);
          } finally {
            await json(emergency, `/admin/users/${reader.id}/external-identities/${identity.id}`, 'DELETE', undefined, 204);
            receipt.domain_identity.active = false;
            await save();
          }
          await json(domainPage, '/users/me', 'GET', undefined, 401);
          assert.equal((await json(emergency, '/users/me')).id, receipt.emergency_admin_id);
          pass('real domain login maps only the owned reader UUID preserves denials and is revoked after unlink');
        }
      }
      const account = receipt.accounts.find(value => value.role === 'viewer');
      await json(emergency, `/admin/users/${account.id}/external-identities/${account.identity_id}`, 'DELETE', undefined, 204);
      try {
        await json(viewer, '/users/me', 'GET', undefined, 401);
        assert.equal((await json(emergency, '/users/me')).id, receipt.emergency_admin_id);
        await providerLogin(providers.get('viewer'), true);
      } finally { await link(emergency, account); }
      await json(viewer, '/users/me', 'GET', undefined, 401);
      pass('unlink revokes the existing OIDC session and preserves local emergency access');
      assert.deepEqual(runtimeErrors, []);
      receipt.last_verification = {source_sha: platform.source_sha, verified_at: new Date().toISOString(), domain_checked: Boolean(domain)};
      await save();
    } else {
      stage = 'owned fixture cleanup';
      for (const account of receipt.accounts) {
        const user = await ownedUser(emergency, account, true);
        if (!user) continue;
        account.id = user.id;
        const identities = await json(emergency, `/admin/users/${account.id}/external-identities`);
        assert.ok(identities.every(value => value.id === account.identity_id || (
          account.role === 'viewer' && receipt.domain_identity?.user_id === account.id &&
          (!receipt.domain_identity.id || value.id === receipt.domain_identity.id) &&
          value.subject === receipt.domain_identity.subject && value.issuer === origin + '/idp/realms/acti'
        ) || (
          !account.identity_id && value.subject === providers.get(account.role).subject &&
          value.issuer === origin + '/idp/realms/acti'
        )), 'No foreign links on the owned pilot');
        for (const identity of identities.filter(value => value.active))
          await json(emergency, `/admin/users/${account.id}/external-identities/${identity.id}`, 'DELETE', undefined, 204);
        if (user.active) await json(emergency, `/admin/users/${account.id}`, 'PATCH', {active: false});
        assert.equal((await ownedUser(emergency, account)).active, false);
      }
      const registries = await json(emergency, '/registries');
      const registry = registries.find(value => receipt.registry ? value.id === receipt.registry.id : value.code === receipt.prefix);
      if (registry) {
        assert.equal(registry?.code, receipt.prefix);
        assert.equal(registry?.description, receipt.run_id);
        receipt.registry = registry;
        await json(emergency, `/admin/access/${registry.id}`, 'PUT', {entries: []});
        assert.deepEqual((await json(emergency, `/admin/access/${registry.id}`)).entries, []);
      }
      receipt.state = 'cleaned';
      if (receipt.domain_identity) receipt.domain_identity.active = false;
      receipt.cleanup_verified = true;
      await save();
      pass('only owned pilot users disabled identities unlinked and synthetic registry grants removed');
    }
    summary = {status: 'PASS', phase, source_sha: platform.source_sha, scenarios: results,
      browser_runtime_errors: runtimeErrors.length, domain_checked: phase === 'verify' && Boolean(domain),
      domain_reader_link_checked: phase === 'verify' && Boolean(domain?.subject),
      cleanup_verified: receipt.cleanup_verified === true};
  } catch (error) {
    await fs.writeFile(path.join(outputDir, phase + '-failure.json'), JSON.stringify({
      status: 'FAIL', phase, stage, error: error.name, scenarios: results,
    }, null, 2) + '\n', {mode: 0o600});
    throw new Error('Task011 OIDC acceptance failed; inspect the redacted stage receipt');
  } finally {
    // Server-side sessions are revoked too; no cookie values are exported or saved.
    const failures = [];
    for (const page of pages) {
      try {
        const current = new URL(page.url());
        if (current.origin === origin) {
          const status = await page.evaluate(async () => (await fetch('/api/v1/users/me')).status);
          if (status === 200) await json(page, '/auth/logout', 'POST', undefined, 204);
        }
      } catch { failures.push('server_session_cleanup'); }
      finally {
        try { await page.context().close(); }
        catch { failures.push('browser_context_cleanup'); }
      }
    }
    if (failures.length) {
      await fs.writeFile(path.join(outputDir, phase + '-session-cleanup.json'), JSON.stringify({
        status: 'FAIL', failures,
      }, null, 2) + '\n', {mode: 0o600});
      throw new Error('Task011 session cleanup failed; inspect the redacted receipt');
    }
    if (summary) summary.session_cleanup_verified = true;
  }
  await fs.writeFile(path.join(outputDir, phase + '-acceptance.json'), JSON.stringify(summary, null, 2) + '\n', {mode: 0o600});
  return summary;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  let browser;
  try {
    const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
    const phase = process.argv[3];
    const admin = JSON.parse(await fs.readFile(config.adminCredentialsFile, 'utf8'));
    const idp = JSON.parse(await fs.readFile(config.idpCredentialsFile, 'utf8'));
    const domainInput = config.domainCredentialsFile ? JSON.parse(await fs.readFile(config.domainCredentialsFile, 'utf8')) : null;
    const domain = domainInput ? {...domainInput, login: domainInput.login ?? domainInput.reader_sam} : null;
    const {chromium} = await import(config.playwrightModule ? pathToFileURL(config.playwrightModule).href : 'playwright');
    browser = await chromium.launch({headless: true, ...(config.executablePath ? {executablePath: config.executablePath} : {})});
    const result = await runTask011Oidc({...config, browser, admin, idp, phase, domain});
    console.log('Task011 genuine HTTPS OIDC ' + phase + ' PASS: ' + result.scenarios.length + ' scenarios.');
  } catch {
    console.error('Task011 OIDC browser FAIL; inspect private redacted diagnostics.');
    process.exitCode = 1;
  } finally { await browser?.close(); }
}
