/** Real Task006 HTTPS relations acceptance. Uses the Task005 private config shape.
 * Synthetic fixtures only; no response interception, SQL Server or external fetching.
 * Run after deployment: node scripts/e2e_task006.mjs <ignored-config.json>
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomBytes} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

async function helpers(browser, origin, outputDir) {
  assert.equal(new URL(origin).protocol, 'https:');
  await fs.mkdir(outputDir, { recursive: true });
  const contexts = [];
  const runtimeErrors = [];
  const results = [];
  let stage = 'browser setup';
  async function pageFor() {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: false });
    contexts.push(context);
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => runtimeErrors.push(error.name));
    return page;
  }
  async function json(page, endpoint, method = 'GET', data, expected = 200) {
    stage = `API ${method} ${endpoint.split('?')[0]}`;
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
    stage = 'role UI login';
    await page.goto(origin);
    await page.getByRole('textbox', { name: 'Логин', exact: true }).fill(credentials.login);
    await page.getByLabel('Пароль', { exact: true }).fill(credentials.password);
    await page.getByRole('button', { name: 'Войти в рабочее пространство' }).click();
    await page.getByRole('button', { name: 'Выйти', exact: true }).waitFor();
  }
  async function mutation(page, endpoint, action, method = 'POST', expected = 200) {
    stage = `UI ${method} ${endpoint} action`;
    const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1' + endpoint && response.request().method() === method);
    const [response] = await Promise.all([pending, action()]);
    assert.equal(response.status(), expected, `${method} ${endpoint} UI status`);
    const uploaded = endpoint.match(/^\/documents\/([0-9a-f-]{36})\/files$/i);
    if (method === 'POST' && expected === 201 && uploaded) {
      // Chromium may evict binary-upload response bodies; verify canonical state after success.
      const card = await json(page, `/documents/${uploaded[1]}`);
      assert.equal(card.id, uploaded[1]);
      assert.ok(card.files.length > 0);
      return card;
    }
    stage = `UI ${method} ${endpoint} response JSON after verified ${expected}`;
    return expected === 204 ? null : response.json();
  }
  async function download(page, buttonName, filename) {
    stage = `UI download ${filename}`;
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
  return { pageFor, json, login, mutation, download, pass, contexts, runtimeErrors, results, getStage: () => stage };
}

export async function runTask006({browser,origin,admin,outputDir,fixturesDir}) {
  const h=await helpers(browser,origin,outputDir);
  const {pageFor,json,login,mutation,pass}=h;
  const suffix=Date.now().toString(36),code=`task006_${suffix}`,title=`Task006 ${suffix}`;
  const accounts=[],sensitive=[admin.password],temporaryRegistries=new Set(),receipt={};
  let ap;
  try {
    ap=await pageFor();await login(ap,admin);
    const platform=await json(ap,'/admin/platform'),administrator=await json(ap,'/users/me');
    pass('administrator strict HTTPS login and deployed source',{source_sha:platform.source_sha});
    const type=await json(ap,'/document-types','POST',{code,name:title},201);
    const version=(await json(ap,`/document-types/${type.id}/versions`))[0];
    await json(ap,`/form-schemas/${version.id}`,'PUT',{schema:{sections:[{key:'main',title:'Основные сведения',fields:[
      {key:'title',label:'Название Task006',type:'text',system:'title',required:true,editable:true,filterable:true,exportable:true},
      {key:'status',label:'Официальный статус Task006',type:'text',system:'status',editable:true},
      {key:'public_file',label:'Оригинал Task006',type:'file',editable:true},
    ]}]}});
    await json(ap,`/form-schemas/${version.id}/publish`,'POST',{});
    await ap.getByRole('button',{name:'Реестры',exact:true}).click();
    await ap.getByRole('button',{name:'Создать реестр',exact:true}).click();
    await ap.getByLabel('Название реестра',{exact:true}).fill(title);
    await ap.getByLabel('Код реестра',{exact:true}).fill(code);
    await ap.getByRole('group',{name:'Доступные типы документов',exact:true}).getByLabel(title,{exact:true}).check();
    const registry=await mutation(ap,'/registries',()=>ap.getByRole('button',{name:'Сохранить реестр',exact:true}).click(),'POST',201);
    // Administrative configuration alone grants no access to document content.
    assert.equal((await json(ap,`/documents?registry_id=${registry.id}`)).total,0);
    const roles={};
    for(const role of ['editor','viewer']) {
      const temporary=randomBytes(24).toString('base64url'),password=randomBytes(24).toString('base64url');sensitive.push(temporary,password);
      const account=await json(ap,'/admin/users','POST',{login:`${code}_${role}`,display_name:`${title} ${role}`,password:temporary,roles:[role]},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:`${code}_${role}`,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);await page.getByLabel('Новый пароль',{exact:true}).fill(password);await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();await page.getByRole('heading',{name:'Мои реестры',exact:true}).waitFor();
      roles[role]={page,account,permissions:role==='editor'?['read','create','update','upload','download']:['read','download']};
    }
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:[{user_id:administrator.id,permissions:['read','download']},...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});
    const ep=roles.editor.page,vp=roles.viewer.page;
    async function open(page,documentId,workspace='work') {await page.goto(`${origin}/${workspace}?document=${documentId}`);await page.getByRole('button',{name:'Связанные акты',exact:true}).waitFor();}
    async function createAct(name) {
      await ep.goto(`${origin}/work`);await ep.getByRole('button',{name:title,exact:true}).click();
      await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(type.id);
      await ep.getByLabel('Название Task006',{exact:false}).fill(name);await ep.getByLabel('Официальный статус Task006',{exact:false}).fill('Действует');
      await ep.getByLabel('Оригинал Task006',{exact:false}).setInputFiles(path.join(fixturesDir,'public.docx'));
      return mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);
    }
    const original=await createAct(`${title} исходный акт`),changing=await createAct(`${title} изменяющий акт`),repealing=await createAct(`${title} отменяющий акт`);
    async function waitContent(documentId) {
      const deadline=Date.now()+120000;
      while(Date.now()<deadline) {
        const content=await json(ep,`/documents/${documentId}/content`),item=content.items.find(row=>row.name==='public.docx');
        if(item?.state==='completed') {assert.ok(item.blocks.some(block=>block.kind==='paragraph'));return item;}
        if(item&&['failed','unsupported','encrypted','no_text'].includes(item.state))throw new Error(`Synthetic DOCX extraction: ${item.state}/${item.error_code}`);
        await delay(1000);
      }
      throw new Error('Real DOCX worker extraction timed out');
    }
    const originals=await waitContent(original.id),basis=await waitContent(changing.id);await waitContent(repealing.id);
    pass('administrator registry configuration and editor creates three acts with real DOCX extraction');
    // Task006 domain scenarios follow the frontend's stable accessible selectors.
    const sourceBlock=basis.blocks.find(block=>block.kind==='paragraph'),targetBlock=originals.blocks.find(block=>block.kind==='paragraph');
    const changeInput={source_document_id:changing.id,target_document_id:original.id,relation_type:'changes',source:'manual',source_version_id:basis.version_id,source_block_id:sourceBlock.id,target_version_id:originals.version_id,target_block_id:targetBlock.id,description:'Синтетическое основание изменения Task006'};
    const change=await proposeUI(changeInput);
    assert.equal(change.state,'proposed');
    const proposed=await json(vp,`/documents/${original.id}/relations`);assert.equal(proposed.status_effects.length,0);
    assert.equal((await json(vp,`/documents/${original.id}`)).status,'Действует');
    for(const page of [ep,vp,ap])await json(page,`/document-relations/${change.id}/confirm`,'POST',{reason:'Нет полномочия',effective_date:'2026-01-01'},403);
    pass('unconfirmed relation preserves official status and explicit permission denies editor reader and administrator confirmation');
    const suggestionInput={source_document_id:repealing.id,target_document_id:original.id,relation_type:'repeals',source:'ai_suggested',description:'Неподтверждённое предложение, не юридическое состояние'};
    const suggestion=await json(ep,'/document-relations','POST',suggestionInput,201);assert.equal(suggestion.state,'proposed');
    assert.equal((await json(vp,`/documents/${original.id}/relations`)).status_effects.length,0);
    // Grant confirmation through ordinary administration UI, never through an admin bypass.
    await ap.getByRole('button',{name:'Роли и доступ',exact:true}).click();await ap.getByLabel('Реестр',{exact:false}).selectOption(registry.id);
    await ap.getByLabel(`${roles.editor.account.display_name}: Подтверждение связей документов`,{exact:true}).check();
    await mutation(ap,`/admin/access/${registry.id}`,()=>ap.getByRole('button',{name:'Сохранить права',exact:true}).click(),'PUT',200);
    await decideUI(change,'confirm','Подтверждено по синтетическому исходному фрагменту');
    await decideUI(suggestion,'reject','Предположение отклонено уполномоченным редактором');
    const repeal=await proposeUI({source_document_id:repealing.id,target_document_id:changing.id,relation_type:'repeals',source:'manual',description:'Синтетическое основание отмены Task006'});
    await decideUI(repeal,'confirm','Подтверждение второй связи в цепочке');
    const changed=await json(vp,`/documents/${original.id}/relations`),repealed=await json(vp,`/documents/${changing.id}/relations`);
    assert.ok(changed.status_effects.some(effect=>effect.relation_id===change.id&&effect.relation_type==='changes'&&effect.applies_now));
    assert.ok(repealed.status_effects.some(effect=>effect.relation_id===repeal.id&&effect.relation_type==='repeals'&&effect.applies_now));
    assert.equal(changed.status_effects.some(effect=>effect.relation_id===suggestion.id),false);
    const history=await json(vp,`/documents/${original.id}/relation-history`);
    assert.ok(history.items.some(event=>event.relation_id===change.id&&event.action==='confirmed'));
    assert.ok(history.items.some(event=>event.relation_id===suggestion.id&&event.action==='rejected'));
    pass('authorized editor confirms change repeal and rejects suggestion with immutable status history');
    await json(ep,'/document-relations','POST',changeInput,409);
    await json(ep,'/document-relations','POST',{...changeInput,source_document_id:original.id},422);
    await json(ep,'/document-relations','POST',{source_document_id:original.id,target_document_id:repealing.id,relation_type:'changes',source:'manual'},409);
    await json(ep,'/document-relations','POST',{...changeInput,source_version_id:originals.version_id,source_block_id:undefined,relation_type:'reference'},422);
    await json(ep,'/document-relations','POST',{source_document_id:original.id,target_document_id:changing.id,relation_type:'not_a_relation',source:'manual'},422);
    pass('real server rejects duplicates self links legal cycles invalid types and foreign document versions');
    const externalTarget={url:'https://example.org/synthetic-act',title:'Проверяемый внешний синтетический акт',identifier:`EXT-${suffix}`,version:'2026-01',effective_date:'2026-01-01',verified:true};
    await json(ep,'/document-relations','POST',{source_document_id:original.id,relation_type:'reference',source:'manual',external_target:{...externalTarget,url:'http://example.org/synthetic-act'}},422);
    const external=await proposeUI({source_document_id:original.id,relation_type:'reference',source:'manual',external_target:externalTarget,description:'Реквизиты и дата внешнего акта; без загрузки правовой базы'});
    assert.deepEqual(external.external_target,externalTarget);
    pass('external HTTPS act records explicit identifier version and effective date while rejecting unsafe URL');
    await open(ep,changing.id);await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();
    await mutation(ep,`/documents/${changing.id}/files`,async()=>ep.getByLabel('Добавить файл или следующую версию',{exact:false}).setInputFiles({name:'public.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:await fs.readFile(path.join(fixturesDir,'private.docx'))}),'POST',201);
    const pinned=await json(vp,`/document-relations/${change.id}/basis?side=source&mode=viewer`);
    assert.equal(pinned.anchor.version_id,basis.version_id);assert.equal(pinned.anchor.version,1);assert.equal(pinned.anchor.block_id,sourceBlock.id);
    assert.ok(pinned.files.some(file=>file.id===basis.file_id&&file.version===1));assert.equal(pinned.blocks[0].id,sourceBlock.id);
    pass('new file version retains the relation exact old version and source paragraph');
    await open(vp,original.id,'view');await vp.getByRole('button',{name:'Связанные акты',exact:true}).click();
    await vp.locator('.relation-row').filter({hasText:changeInput.description}).waitFor();
    await vp.getByLabel('Фильтр типа связи',{exact:false}).selectOption('changes');
    await vp.locator('.relation-graph').getByRole('button',{name:changing.title,exact:false}).first().waitFor();
    await vp.getByText('Связей: 1',{exact:true}).waitFor();assert.equal(await vp.locator('.relation-row').count(),1);
    await vp.screenshot({path:path.join(outputDir,'reader-graph-viewport.png'),fullPage:false});
    await vp.locator('.relation-graph').getByRole('button',{name:changing.title,exact:false}).first().click();
    await vp.getByRole('heading',{name:changing.title,exact:true}).waitFor();
    await open(vp,original.id,'view');await vp.getByRole('button',{name:'История статуса',exact:true}).click();
    await vp.getByRole('heading',{name:'Связь подтверждена',exact:true}).first().waitFor();
    await vp.getByRole('heading',{name:'Связь отклонена',exact:true}).first().waitFor();
    pass('reader accessible graph node navigation type filter and proposed confirmed rejected history');
    // Exact basis UI is checked after a second original was uploaded, preserving version 1.
    await verifyBasisUI(change,sourceBlock);
    await open(vp,original.id,'view');await vp.getByRole('button',{name:'Связанные акты',exact:true}).click();
    const outsideLink=vp.getByRole('link',{name:externalTarget.title,exact:true});await outsideLink.waitFor();
    assert.equal(await outsideLink.getAttribute('href'),externalTarget.url);assert.match(await outsideLink.getAttribute('rel'),/noopener/);
    assert.equal(await vp.getByRole('button',{name:'Предложить связь документа',exact:true}).count(),0);
    await json(vp,'/document-relations','POST',{source_document_id:original.id,target_document_id:repealing.id,relation_type:'topic',source:'manual',description:'Читатель не предлагает'},403);
    await json(vp,`/documents/${original.id}`,'PUT',{revision:original.revision,values:{title:'Forbidden reader edit'}},403);
    const beforeGraph=await json(vp,`/documents/${original.id}/relation-graph`);assert.ok(beforeGraph.nodes.some(node=>node.id===changing.id));
    await json(ap,`/documents/${changing.id}/acl`,'PUT',{entries:[{user_id:roles.editor.account.id,permissions:['read','update','upload','download','confirm_relations']}]});
    for(const endpoint of [`/documents/${original.id}/relations`,`/documents/${original.id}/relation-graph`,`/documents/${original.id}/relation-history`]) {
      const visible=await json(vp,endpoint),serialized=JSON.stringify(visible);
      assert.equal(serialized.includes(changing.id),false);assert.equal(serialized.includes(changing.title),false);
      if(endpoint.endsWith('/relations')) {assert.equal(visible.items.some(item=>item.id===change.id),false);assert.equal(visible.status_effects.some(effect=>effect.relation_id===change.id),false);assert.equal(visible.total,visible.items.length);}
    }
    for(const page of [vp,ap])await json(page,`/document-relations/${change.id}`,'GET',undefined,404);
    await json(vp,`/document-relations/${change.id}/basis?side=source&mode=viewer`,'GET',undefined,404);
    await json(vp,`/documents/${changing.id}`,'GET',undefined,404);
    await json(vp,`/documents/${changing.id}/relation-options`,'GET',undefined,404);
    assert.equal((await json(vp,`/documents?registry_id=${registry.id}&q=${encodeURIComponent(changing.title)}`)).total,0);
    await open(vp,original.id,'view');await vp.getByRole('button',{name:'Связанные акты',exact:true}).click();
    await vp.getByText(`Связей: ${(await json(vp,`/documents/${original.id}/relations`)).total}`,{exact:true}).waitFor();
    assert.equal((await vp.locator('.document-card').innerText()).includes(changing.title),false);
    await vp.screenshot({path:path.join(outputDir,'reader-acl-viewport.png'),fullPage:false});
    pass('node ACL revocation hides graph names identifiers counts effects history and direct basis from reader and administrator');
    const legacyType=(await json(ap,'/document-types')).find(value=>value.code==='acti-v2'),legacyRegistry=(await json(ap,'/registries')).find(value=>value.document_type_ids.includes(legacyType.id));
    const previous=await json(ap,`/admin/access/${legacyRegistry.id}`);await json(ap,`/admin/access/${legacyRegistry.id}`,'PUT',{entries:[...previous.entries,...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});temporaryRegistries.add(legacyRegistry.id);
    await ep.goto(`${origin}/work`);await ep.getByRole('button',{name:legacyRegistry.name,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(legacyType.id);await ep.getByLabel('Наименование документа',{exact:false}).fill(`${title} legacy smoke`);await ep.getByLabel('Регистрационный номер',{exact:true}).fill('000006');await ep.getByRole('combobox',{name:'Тип документа ACTI v2',exact:true}).selectOption({label:'Постановление'});await ep.getByLabel('Номер тома',{exact:true}).fill('1-2');
    const legacyCard=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);assert.equal(legacyCard.reg_number,'000006');assert.equal(legacyCard.values.should_publish??null,null);assert.equal(legacyCard.values.volume_number,'1-2');assert.match(legacyCard.values.type_id,/^[0-9a-f-]{36}$/i);
    await vp.goto(`${origin}/view?document=${legacyCard.id}`);await vp.getByText('000006',{exact:true}).first().waitFor();await vp.getByText('Постановление',{exact:true}).first().waitFor();await vp.getByText('1-2',{exact:true}).first().waitFor();
    pass('ACTI v2 number nullable publication reference and volume remain compatible');
    for(const page of [ep,vp]) {await page.goto(`${origin}/admin`);await page.waitForURL(url=>url.pathname!='/admin');await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('heading',{name:'Вход в ACTI',exact:true}).waitFor();await json(page,'/users/me','GET',undefined,401);}
    assert.deepEqual(h.runtimeErrors,[]);pass('reader editor forbidden administration actual logout and no browser application errors');
    Object.assign(receipt,{status:'PASS',source_sha:platform.source_sha,scenarios:h.results,synthetic:{registry_id:registry.id,document_ids:[original.id,changing.id,repealing.id],relation_ids:[change.id,repeal.id,suggestion.id,external.id]}});return receipt;

    async function proposeUI(input) {
      await open(ep,input.source_document_id);await ep.getByRole('button',{name:'Связанные акты',exact:true}).click();
      await ep.getByRole('button',{name:'Предложить связь документа',exact:true}).click();const form=ep.locator('form.relation-editor');
      await form.getByLabel('Тип связи',{exact:false}).selectOption(input.relation_type);
      async function anchor(prefix,versionId,blockId,documentId) {
        if(!versionId)return;
        await form.getByLabel(`Версия: ${prefix}`,{exact:false}).selectOption(versionId);
        if(blockId){const content=await json(ep,`/documents/${documentId}/content`),extraction=content.items.find(item=>item.blocks.some(block=>block.id===blockId));assert.ok(extraction);await form.getByLabel(`Оригинал: ${prefix}`,{exact:false}).selectOption(extraction.id);await form.getByLabel(`Фрагмент: ${prefix}`,{exact:false}).selectOption(blockId);}
      }
      await anchor('Исходный документ',input.source_version_id,input.source_block_id,input.source_document_id);
      if(input.external_target) {
        await form.getByLabel('Внешний акт',{exact:true}).check();
        const labels={title:'Название внешнего акта',url:'HTTPS-ссылка на акт',identifier:'Реквизиты внешнего акта',version:'Версия внешнего акта',effective_date:'Дата действия внешнего акта'};
        for(const [key,label] of Object.entries(labels))await form.getByLabel(label,{exact:true}).fill(input.external_target[key]);
        await form.getByLabel('Я проверил ссылку, реквизиты и версию источника',{exact:true}).check();
      } else {
        const target=await json(ep,`/documents/${input.target_document_id}`);await form.getByLabel('Поиск связанного документа',{exact:true}).fill(target.title);
        await form.getByRole('combobox',{name:/^Связанный документ/}).selectOption(input.target_document_id);
        await anchor('Связанный документ',input.target_version_id,input.target_block_id,input.target_document_id);
      }
      await form.getByLabel('Пояснение связи',{exact:true}).fill(input.description);
      const relation=await mutation(ep,'/document-relations',()=>form.getByRole('button',{name:'Предложить связь',exact:true}).click(),'POST',201);
      await form.waitFor({state:'hidden'});return relation;
    }
    async function decideUI(relation,decision,reason) {
      await open(ep,relation.source_document.id);await ep.getByRole('button',{name:'Связанные акты',exact:true}).click();
      const row=ep.locator('.relation-row').filter({hasText:relation.description});
      await row.getByRole('button',{name:decision==='confirm'?'Подтвердить связь':'Отклонить связь',exact:true}).click();
      await row.getByLabel('Основание решения',{exact:true}).fill(reason);await row.getByLabel('Дата действия решения',{exact:true}).fill('2026-01-01');
      await mutation(ep,`/document-relations/${relation.id}/${decision}`,()=>row.getByRole('button',{name:'Сохранить решение о связи',exact:true}).click(),'POST',200);
      await row.getByText(decision==='confirm'?'Подтверждена':'Отклонена',{exact:true}).waitFor();
    }
    async function verifyBasisUI(relation,block) {
      await open(vp,original.id,'view');await vp.getByRole('button',{name:'Основания и изменения',exact:true}).click();
      const row=vp.locator('.relation-row').filter({hasText:relation.description});
      const panel=row.locator('details.relation-basis').filter({has:vp.locator('summary').filter({hasText:'Основание исходного документа'})});
      await panel.locator('summary').click();
      await panel.getByText(`${changing.title} · оригинал версии 1`,{exact:true}).waitFor();
      assert.ok((await panel.locator('blockquote.content-text').innerText()).includes(block.text));
      const pending=vp.waitForEvent('download');await panel.getByRole('button',{name:'Скачать основание версии 1',exact:true}).click();
      const originalDownload=await pending;assert.equal(await originalDownload.failure(),null);
      const destination=path.join(outputDir,'relation-original-version-1.docx');await originalDownload.saveAs(destination);
      assert.deepEqual(await fs.readFile(destination),await fs.readFile(path.join(fixturesDir,'public.docx')));
      await panel.getByRole('button',{name:'Перейти к документу основания',exact:true}).click();
      await vp.locator('.content-block.selected').waitFor();assert.ok((await vp.locator('.content-block.selected').innerText()).includes(block.text));
      await vp.screenshot({path:path.join(outputDir,'reader-old-basis-viewport.png'),fullPage:false});
      pass('reader opens and downloads pinned version 1 basis and navigates to the exact original paragraph');
    }

  } catch(error) {
    const failure={stage:h.getStage(),message:String(error.message),stack:String(error.stack||'')};
    await fs.writeFile(path.join(outputDir,'failure.txt'),sensitive.reduce((message,value)=>message.split(value).join('[redacted]'),JSON.stringify(failure,null,2)));
    console.error(`Task006 failed operation: ${failure.stage}`);
    let index=0;for(const context of h.contexts)for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`failure-page-${index++}.png`),fullPage:false}).catch(()=>{});
    throw error;
  } finally {
    const failures=[];let accountsVerified=false;
    if(ap)for(const registryId of temporaryRegistries)try {const current=await json(ap,`/admin/access/${registryId}`);await json(ap,`/admin/access/${registryId}`,'PUT',{entries:current.entries.filter(value=>!accounts.includes(value.user_id))});const restored=await json(ap,`/admin/access/${registryId}`);assert.equal(restored.entries.some(value=>accounts.includes(value.user_id)),false);}catch {failures.push('temporary registry grants');}
    if(ap)for(const id of accounts)try {await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}catch {failures.push('synthetic account deactivation');}
    if(ap&&accounts.length)try {const users=await json(ap,'/admin/users');assert.ok(accounts.every(id=>users.some(user=>user.id===id&&!user.active)));accountsVerified=true;}catch {failures.push('synthetic account verification');}
    receipt.cleanup_verified=accountsVerified&&failures.length===0;
    for(const context of h.contexts)await context.close();await fs.writeFile(path.join(outputDir,'scenarios.json'),JSON.stringify(h.results,null,2));await fs.writeFile(path.join(outputDir,'cleanup.json'),JSON.stringify({status:failures.length?'FAIL':'PASS',cleanup_verified:receipt.cleanup_verified,accounts_disabled:accounts.length,temporary_grants_removed:temporaryRegistries.size,failures},null,2));assert.deepEqual(failures,[],'Synthetic cleanup must succeed');
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runTask006({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Task006 actual HTTPS browser PASS: ${receipt.scenarios.length} scenarios.`);}catch(error){console.error(`Task006 browser FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
