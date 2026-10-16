/** Task010 real HTTPS service consumer acceptance. Private config matches Task005.
 * One-time bearer credentials remain in memory; no traces, headers or token screenshots.
 * No API interception, response mocks, direct database access or model execution.
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

export async function runTask010({browser,request,origin,admin,outputDir,fixturesDir}) {
  const h=await helpers(browser,origin,outputDir),{pageFor,json,login,mutation,pass}=h;
  const suffix=Date.now().toString(36),code=`task010_${suffix}`,title=`Task010 ${suffix}`;
  const accounts=[],services=[],serviceUsers=[],clients=[],sensitive=[admin.password],temporaryRegistries=new Set(),receipt={};
  let ap,vp,ep,stage='setup';
  const servicePrefix='/api/service/v1';
  const redact=value=>sensitive.reduce((message,secret)=>message.split(secret).join('[redacted]'),String(value)).replace(/Bearer\s+[^\s"']+/gi,'Bearer [redacted]');
  async function consumer(secret) {
    // A standalone request context has no browser cookies; use headers, never query parameters.
    const client=await request.newContext({baseURL:origin,ignoreHTTPSErrors:false,extraHTTPHeaders:{Accept:'application/json',Authorization:`Bearer ${secret}`},maxRedirects:0,timeout:20000});
    clients.push(client);return client;
  }
  async function call(client,endpoint,{expected=200,method='GET',body,binary=false}={}) {
    stage=`consumer ${method} ${endpoint.split('?')[0]}`;
    let response;
    try {response=await client.fetch(servicePrefix+endpoint,{method,...(body===undefined?{}:{data:body}),failOnStatusCode:false});}
    catch {throw new Error(`Consumer transport failed at ${method} ${endpoint.split('?')[0]}`);}
    assert.equal(response.status(),expected,`Consumer ${method} ${endpoint.split('?')[0]} status`);
    assert.ok(response.headers()['x-request-id'],'Service response has request ID');
    const result=binary?await response.body():await response.json().catch(()=>null);await response.dispose();return result;
  }
  async function waitContent(page,id,fileId) {
    const deadline=Date.now()+120000;let extraction;
    while(Date.now()<deadline){extraction=(await json(page,`/documents/${id}/content`)).items.find(item=>!fileId||item.file_id===fileId);if(extraction?.state==='completed')return extraction;if(extraction&&['failed','unsupported','encrypted','no_text'].includes(extraction.state))throw new Error(`Synthetic source extraction ${extraction.state}`);await delay(1000);}
    throw new Error(`Synthetic source worker timed out with ${extraction?.state||'pending'}`);
  }
  async function eventWindow(client,{cursor=null,untilId=null,pages=8}={}) {
    const items=[];
    for(let page=0;page<pages;page++) {
      const requestCursor=cursor,result=await call(client,`/events?page_size=10${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`);
      assert.deepEqual(Object.keys(result).sort(),['items','next_cursor']);assert.ok(result.next_cursor);items.push(...result.items);cursor=result.next_cursor;
      if(untilId&&result.items.some(item=>JSON.stringify(item).includes(untilId)))return {items,bookmark:requestCursor};
      // An empty filtered page may have scanned hidden history. It is a bookmark, not EOF.
      if(!result.items.length)await delay(200);
    }
    assert.equal(untilId,null,'Known synthetic event reached within bounded scan');return {items,bookmark:cursor};
  }
  try {
    ap=await pageFor();await login(ap,admin);const platform=await json(ap,'/admin/platform'),administrator=await json(ap,'/users/me');
    pass('administrator strict HTTPS cookie login and deployed source',{source_sha:platform.source_sha});
    const type=await json(ap,'/document-types','POST',{code,name:`${title} контрактная форма`},201),form=(await json(ap,`/document-types/${type.id}/versions`))[0];
    await json(ap,`/form-schemas/${form.id}`,'PUT',{schema:{sections:[{key:'main',title:'Синтетический API источник',fields:[{key:'title',label:'Название Task010',type:'text',system:'title',editable:true,required:true,filterable:true},{key:'secret',label:'Закрытое поле Task010',type:'text',editable:true,read_roles:['editor'],write_roles:['editor']},{key:'public_file',label:'Открытый оригинал Task010',type:'file',editable:true},{key:'private_file',label:'Закрытый оригинал Task010',type:'file',editable:true,read_roles:['editor'],write_roles:['editor']}]}]}});
    await json(ap,`/form-schemas/${form.id}/publish`,'POST',{});
    const registry=await json(ap,'/registries','POST',{code,name:`${title} API реестр`,document_type_ids:[type.id]},201),outside=await json(ap,'/registries','POST',{code:`${code}_outside`,name:`${title} другой реестр`,document_type_ids:[type.id]},201);
    const roles={};
    for(const role of ['editor','viewer']) {
      const temporary=randomBytes(24).toString('base64url'),password=randomBytes(24).toString('base64url');sensitive.push(temporary,password);
      const account=await json(ap,'/admin/users','POST',{login:`${code}_${role}`,display_name:`${title} ${role}`,password:temporary,roles:[role]},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:`${code}_${role}`,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);await page.getByLabel('Новый пароль',{exact:true}).fill(password);await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();await page.getByRole('heading',{name:'Мои реестры',exact:true}).waitFor();
      roles[role]={page,account,permissions:role==='editor'?['read','create','update','upload','download']:['read','download']};
    }
    ep=roles.editor.page;vp=roles.viewer.page;
    for(const scope of [registry,outside])await json(ap,`/admin/access/${scope.id}`,'PUT',{entries:[{user_id:administrator.id,permissions:['read','create','update','upload','download']},...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});
    const original=await fs.readFile(path.join(fixturesDir,'public.docx')),newOriginal=await fs.readFile(path.join(fixturesDir,'private.docx'));
    async function createDocument(name,scope=registry,withFiles=true) {return json(ep,'/documents','POST',{registry_id:scope.id,document_type_id:type.id,schema_version_id:form.id,values:{title:name,secret:'СЕКРЕТНЫЙМАРКЕРTASK010'},files:withFiles?[{field_key:'public_file',name:'public.docx',mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',content_base64:original.toString('base64')},{field_key:'private_file',name:'hidden-task010.docx',mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',content_base64:newOriginal.toString('base64')}]:[]},201);}
    const source=await createDocument(`${title} источник`),target=await createDocument(`${title} связанный акт`,registry,false),closed=await createDocument(`${title} чужой scope`,outside,false);
    const extraction=await waitContent(ep,source.id,source.files.find(file=>file.field_key==='public_file').id),privateExtraction=await waitContent(ep,source.id,source.files.find(file=>file.field_key==='private_file').id),block=extraction.blocks.find(item=>item.kind==='paragraph'&&item.text.length>20)||extraction.blocks[0];
    assert.ok(block.text.includes(' '));
    const relation=await json(ep,'/document-relations','POST',{source_document_id:source.id,target_document_id:target.id,relation_type:'reference',source_version_id:extraction.version_id,source_block_id:block.id,description:'Синтетическая ссылка API'},201);
    const process=await json(ap,'/processes','POST',{registry_id:registry.id,code,name:`${title} процесс`},201),processDraft=(await json(ap,`/processes/${process.id}/versions`))[0];
    const processSaved=await json(ap,`/process-versions/${processDraft.id}`,'PUT',{revision:processDraft.revision,effective_from:'2026-01-01',steps:[{key:'source',title:'Прочитать источник',instruction:'Синтетический шаг API',is_start:true,is_terminal:true,form_schema_id:form.id,sources:[{document_id:source.id,version_id:extraction.version_id,block_id:block.id,note:'Синтетическое основание'}]}],transitions:[]});
    const processVersion=await json(ap,`/process-versions/${processDraft.id}/publish`,'POST',{revision:processSaved.revision});
    const template=await json(ep,'/knowledge/templates','POST',{registry_id:registry.id,name:`${title} шаблон`,purpose:'Синтетическое API получение',visibility:'registry',original:{document_id:source.id,version_id:extraction.version_id,file_id:extraction.file_id}},201),templateVersion=await json(ep,`/knowledge/template-versions/${template.version.id}/publish`,'POST',{revision:template.version.revision});
    const series=await json(ap,'/number-series','POST',{code,name:`${title} номера`,registry_id:registry.id,document_type_ids:[type.id],synthetic:true},201);
    await json(ap,`/number-series/${series.id}/grants`,'PUT',{entries:[{user_id:roles.editor.account.id,permissions:['view','reserve']}]});
    const reserved=await json(ep,`/number-series/${series.id}/reserve`,'POST',{count:1,idempotency_key:`${code}_reserve`});
    pass('real PostgreSQL source extraction pinned relation process template and number fixtures are ready');

    async function servicePage() {stage='administrator service account page';await ap.goto(`${origin}/admin`);await ap.getByRole('button',{name:'API и сервисные учётные записи',exact:true}).click();}
    async function createService(name,operations) {
      await servicePage();await ap.getByRole('button',{name:'Создать сервисную запись',exact:true}).click();
      const editor=ap.locator('form.service-account-editor');await editor.getByLabel('Название интеграции',{exact:true}).fill(name);
      await editor.getByRole('checkbox',{name:registry.name,exact:true}).check();
      for(const operation of operations)await editor.getByRole('checkbox',{name:operation,exact:true}).check();
      const account=await mutation(ap,'/admin/service-accounts',()=>editor.getByRole('button',{name:'Сохранить сервисную запись',exact:true}).click(),'POST',201);services.push(account.id);serviceUsers.push(account.user_id);return account;
    }
    async function selectService(account) {await servicePage();await ap.getByLabel('Фильтр реестра сервисов',{exact:false}).selectOption(registry.id);await ap.getByRole('row').filter({has:ap.getByText(account.name,{exact:true})}).getByRole('button',{name:'Открыть сервис',exact:true}).click();await ap.locator('.service-account-detail').getByRole('heading',{name:account.name,exact:true}).waitFor();}
    async function issue(account,previous=null) {
      await selectService(account);
      if(previous)await ap.locator('.service-account-detail').getByRole('row').filter({has:ap.getByText(previous.prefix,{exact:true})}).getByRole('button',{name:'Ротировать токен',exact:true}).click();
      else await ap.getByRole('button',{name:'Создать токен',exact:true}).click();
      const tokenForm=ap.locator('form.token-action');await tokenForm.getByLabel('Срок токена, дней',{exact:false}).fill('1');await tokenForm.getByLabel(/^Причина операции с токеном/).fill('Синтетическая проверка API Task010');
      const endpoint=`/admin/service-accounts/${account.id}/tokens${previous?`/${previous.id}/rotate`:''}`;
      const data=await mutation(ap,endpoint,()=>tokenForm.getByRole('button',{name:previous?'Выпустить замену токена':'Выпустить токен',exact:true}).click(),'POST',201);
      // Read the actual one-time UI value. Never include either value in assertion diagnostics.
      sensitive.push(data.token);const secret=await ap.getByLabel('Одноразовый показ токена',{exact:true}).inputValue();sensitive.push(secret);assert.ok(typeof secret==='string'&&secret.length>20&&secret===data.token,'One-time UI token agrees with successful issue');
      await ap.getByRole('button',{name:'Закрыть и скрыть токен',exact:true}).click();await ap.locator('.one-time-secret').waitFor({state:'detached'});
      return {id:data.id,prefix:data.prefix,client:await consumer(secret)};
    }
    const service=await createService(`${title} полный читатель`,['Карточки и версии документов','Оригиналы файлов','Содержание и блоки','Связи документов','Описания процессов','Шаблоны и практика','Реестр номеров','События изменений']);
    await json(ap,`/number-series/${series.id}/grants`,'PUT',{entries:[{user_id:roles.editor.account.id,permissions:['view','reserve']},{user_id:service.user_id,permissions:['view']}]});
    let credential=await issue(service);
    const baselineMetrics=await json(ap,`/admin/operations/metrics?registry_id=${registry.id}`);
    async function measureUI(operation,action) {
      const pending=vp.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/usage/retrieval'&&response.request().method()==='POST'&&response.request().postDataJSON()?.operation===operation);
      const [response]=await Promise.all([pending,action()]);assert.equal(response.status(),204);const payload=response.request().postDataJSON();assert.deepEqual(Object.keys(payload).sort(),['duration_ms','operation','registry_id']);assert.equal(payload.registry_id,registry.id);assert.ok(Number.isInteger(payload.duration_ms)&&payload.duration_ms>=0);
    }
    await vp.goto(`${origin}/view`);await vp.getByRole('button',{name:registry.name,exact:true}).click();await vp.getByLabel('Поиск',{exact:true}).fill(source.title);
    await measureUI('search',()=>vp.getByRole('button',{name:'Найти',exact:true}).click());
    await measureUI('time_to_open',()=>vp.getByRole('row').filter({has:vp.getByText(source.title,{exact:true})}).click());
    const measured=await json(ap,`/admin/operations/metrics?registry_id=${registry.id}`);assert.ok(measured.retrieval.time_to_open_count>(baselineMetrics.retrieval.time_to_open_count||0));
    await ap.screenshot({path:path.join(outputDir,'admin-service-scope-no-secret-viewport.png'),fullPage:false});
    pass('administrator creates service scope and issues a one-time credential through UI without recording its secret');
    const serviceCard=await call(credential.client,`/documents/${source.id}`);assert.equal(serviceCard.id,source.id);assert.equal(JSON.stringify(serviceCard).includes('СЕКРЕТНЫЙМАРКЕРTASK010'),false);assert.equal(serviceCard.files.some(file=>file.id===privateExtraction.file_id),false);
    const exactVersion=await call(credential.client,`/file-versions/${extraction.version_id}`);assert.ok(JSON.stringify(exactVersion).includes(extraction.version_id));
    const exactBlock=await call(credential.client,`/blocks/${block.id}`);assert.equal(exactBlock.id,block.id);assert.equal(exactBlock.text,block.text);
    assert.deepEqual(await call(credential.client,`/files/${extraction.file_id}/download`,{binary:true}),original);
    await call(credential.client,`/relations/${relation.id}`);await call(credential.client,`/process-versions/${processVersion.id}/steps/source`);await call(credential.client,`/template-versions/${templateVersion.id}`);
    assert.ok((await call(credential.client,`/number-series/${series.id}/entries`)).items.some(item=>item.id===reserved.items[0].id));
    for(const path of [`/files/${privateExtraction.file_id}/download`,`/blocks/${privateExtraction.blocks[0].id}`,`/documents/${closed.id}`])await call(credential.client,path,{expected:404});
    pass('ordinary bearer consumer retrieves exact allowed version block file relation process template and numbers with field and outside scope ACL');

    // A later original must not retarget an existing immutable source pointer.
    await ep.goto(`${origin}/work?document=${source.id}`);await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();
    const uploaded=await mutation(ep,`/documents/${source.id}/files`,()=>ep.getByLabel('Добавить файл или следующую версию',{exact:false}).setInputFiles({name:'public.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:newOriginal}),'POST',201);
    const nextFile=uploaded.files.slice().sort((a,b)=>b.version-a.version)[0];assert.notEqual(nextFile.id,extraction.file_id);assert.ok(nextFile.version>extraction.version);await waitContent(ep,source.id,nextFile.id);
    assert.deepEqual(await call(credential.client,`/files/${extraction.file_id}/download`,{binary:true}),original);assert.equal((await call(credential.client,`/blocks/${block.id}`)).text,block.text);
    await vp.goto(`${origin}/view?document=${source.id}&tab=content&file=${extraction.file_id}&block=${block.id}&ordinal=${block.ordinal}#block-${block.id}`);await vp.locator('.content-block.selected').waitFor();assert.ok((await vp.locator('.content-block.selected').innerText()).includes(block.text));
    await vp.locator('.content-block.selected').scrollIntoViewIfNeeded();await vp.screenshot({path:path.join(outputDir,'reader-exact-old-block-viewport.png'),fullPage:false});
    await vp.setViewportSize({width:430,height:932});await vp.locator('.content-block.selected').scrollIntoViewIfNeeded();await vp.screenshot({path:path.join(outputDir,'reader-exact-old-block-narrow-viewport.png'),fullPage:false});await vp.setViewportSize({width:1440,height:1000});
    pass('reader opens the exact API source pointer and consumer old version remains byte identical after a later original');

    const restrictedService=await createService(`${title} только метаданные`,['Карточки и версии документов']),restrictedCredential=await issue(restrictedService);
    await call(restrictedCredential.client,`/documents/${source.id}`);await call(restrictedCredential.client,`/blocks/${block.id}`,{expected:403});
    for(const path of ['/api/v1/admin/users','/api/v1/users/me',`/api/v1/documents/${source.id}`]){const response=await restrictedCredential.client.get(path,{failOnStatusCode:false});assert.ok([401,403].includes(response.status()));await response.dispose();}
    await call(credential.client,`/documents/${source.id}`,{method:'POST',body:{},expected:405});
    pass('operation scoped token is read only and cannot obtain browser session administration or existing cookie API authority');

    const beforeWindow=await eventWindow(credential.client,{untilId:source.id,pages:100}),oldCursor=beforeWindow.bookmark;
    for(const event of beforeWindow.items){assert.deepEqual(Object.keys(event).sort(),['created_at','event_type','id','object','origins','payload_version']);assert.equal(event.payload_version,1);assert.equal(JSON.stringify(event).includes(block.text),false);}
    const grants=await json(ap,`/admin/access/${registry.id}`);
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:grants.entries.map(entry=>[service.user_id,roles.viewer.account.id].includes(entry.user_id)?{...entry,permissions:['read']}:entry)});
    await call(credential.client,`/documents/${source.id}`);for(const path of [`/blocks/${block.id}`,`/files/${extraction.file_id}/download`])await call(credential.client,path,{expected:404});
    await json(vp,`/content/extractions/${extraction.id}/blocks`,'GET',undefined,403);
    const redactedProcess=await call(credential.client,`/process-versions/${processVersion.id}`);assert.equal(redactedProcess.steps[0].sources[0].restricted,true);
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:grants.entries.map(entry=>[service.user_id,roles.viewer.account.id].includes(entry.user_id)?{...entry,permissions:[]}:entry)});
    for(const path of [`/documents/${source.id}`,`/blocks/${block.id}`,`/process-versions/${processVersion.id}`,`/template-versions/${templateVersion.id}`])await call(credential.client,path,{expected:404});
    assert.deepEqual((await eventWindow(credential.client,{cursor:oldCursor,pages:3})).items,[]);
    await json(vp,`/documents/${source.id}`,'GET',undefined,404);
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:grants.entries});
    await json(ap,`/documents/${source.id}/acl`,'PUT',{entries:[{user_id:roles.editor.account.id,permissions:['read','update','upload','download']},{user_id:administrator.id,permissions:['read','download']}]});
    for(const path of [`/documents/${source.id}`,`/blocks/${block.id}`,`/files/${extraction.file_id}/download`])await call(credential.client,path,{expected:404});
    const filteredWindow=await eventWindow(credential.client,{cursor:oldCursor,untilId:target.id,pages:20});
    for(const event of filteredWindow.items)for(const hidden of [source.id,extraction.file_id,block.id])assert.equal(JSON.stringify(event).includes(hidden),false);
    await vp.goto(`${origin}/view?document=${source.id}&tab=content&file=${extraction.file_id}&block=${block.id}`);await vp.getByText('Документ не найден',{exact:false}).first().waitFor();
    await vp.screenshot({path:path.join(outputDir,'reader-revoked-source-viewport.png'),fullPage:false});await json(ap,`/documents/${source.id}/acl`,'PUT',{entries:[]});
    pass('current download and document ACL revoke old URLs source references and historical event pointers immediately');

    async function setSourceOperations(enabled) {
      await selectService(service);await ap.getByRole('button',{name:'Изменить сервисную запись',exact:true}).click();const editor=ap.locator('form.service-account-editor');
      for(const label of ['Содержание и блоки','Оригиналы файлов'])await editor.getByRole('checkbox',{name:label,exact:true}).setChecked(enabled);
      await editor.getByLabel(/^Причина изменения области доступа/).fill('Синтетическая проверка текущей области API');await mutation(ap,`/admin/service-accounts/${service.id}`,()=>editor.getByRole('button',{name:'Сохранить сервисную запись',exact:true}).click(),'PATCH',200);
    }
    await setSourceOperations(false);await call(credential.client,`/blocks/${block.id}`,{expected:403});await call(credential.client,`/files/${extraction.file_id}/download`,{expected:403});
    assert.equal((await call(credential.client,`/process-versions/${processVersion.id}`)).steps[0].sources[0].restricted,true);await setSourceOperations(true);await call(credential.client,`/blocks/${block.id}`);
    pass('administrator changes the current service scope through UI and existing token cannot expand process basis through missing file or content operations');

    await selectService(service);const rotated=await issue(service,credential);await call(credential.client,`/documents/${source.id}`,{expected:401});credential=rotated;await call(credential.client,`/blocks/${block.id}`);
    await selectService(service);const tokenRow=ap.locator('.service-account-detail').getByRole('row').filter({has:ap.getByText(credential.prefix,{exact:true})});await tokenRow.getByRole('button',{name:'Отозвать токен',exact:true}).click();await ap.getByLabel(/^Причина операции с токеном/).fill('Завершение синтетической проверки');await mutation(ap,`/admin/service-accounts/${service.id}/tokens/${credential.id}/revoke`,()=>ap.getByRole('button',{name:'Подтвердить отзыв токена',exact:true}).click(),'POST',204);await call(credential.client,`/documents/${source.id}`,{expected:401});
    pass('administrator rotates then revokes the real token through UI old credential is immediately rejected');
    await ap.getByRole('button',{name:'Эксплуатация',exact:true}).click();await ap.getByRole('heading',{name:'Эксплуатация',exact:true}).waitFor();
    const metrics=await json(ap,`/admin/operations/metrics?registry_id=${registry.id}`);assert.ok(metrics.retrieval.requests>baselineMetrics.retrieval.requests);assert.ok(metrics.retrieval.time_to_open_count>(baselineMetrics.retrieval.time_to_open_count||0));assert.ok(metrics.content.documents>=2);for(const hidden of [block.text,'СЕКРЕТНЫЙМАРКЕРTASK010',source.title])assert.equal(JSON.stringify(metrics).includes(hidden),false);
    await ap.getByLabel('Реестр эксплуатационных данных',{exact:false}).selectOption(registry.id);await ap.getByRole('heading',{name:'Состояние разрешённых данных',exact:true}).waitFor();await ap.screenshot({path:path.join(outputDir,'admin-operations-metrics-viewport.png'),fullPage:false});await ap.getByRole('heading',{name:'Обработка событий',exact:true}).waitFor();await ap.getByLabel('Состояние событий',{exact:false}).selectOption('pending');await json(ap,`/admin/operations/queue?registry_id=${registry.id}&state=pending&page_size=20`);
    pass('administrator sees actual aggregate operational metrics and queue diagnostics without source text or secrets');

    const legacyType=(await json(ap,'/document-types')).find(value=>value.code==='acti-v2'),legacyRegistry=(await json(ap,'/registries')).find(value=>value.document_type_ids.includes(legacyType.id)),legacyGrants=await json(ap,`/admin/access/${legacyRegistry.id}`);
    await json(ap,`/admin/access/${legacyRegistry.id}`,'PUT',{entries:[...legacyGrants.entries,...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});temporaryRegistries.add(legacyRegistry.id);
    await ep.goto(`${origin}/work`);await ep.getByRole('button',{name:legacyRegistry.name,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(legacyType.id);await ep.getByLabel('Наименование документа',{exact:false}).fill(`${title} legacy smoke`);await ep.getByLabel('Регистрационный номер',{exact:true}).fill('000010');await ep.getByRole('combobox',{name:'Тип документа ACTI v2',exact:true}).selectOption({label:'Постановление'});await ep.getByLabel('Номер тома',{exact:true}).fill('1-2');const legacyCard=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);
    assert.equal(legacyCard.reg_number,'000010');assert.equal(legacyCard.values.should_publish??null,null);assert.equal(legacyCard.values.volume_number,'1-2');assert.match(legacyCard.values.type_id,/^[0-9a-f-]{36}$/i);
    await vp.goto(`${origin}/view?document=${legacyCard.id}`);await vp.getByText('000010',{exact:true}).first().waitFor();await vp.getByText('Постановление',{exact:true}).first().waitFor();await vp.getByText('1-2',{exact:true}).first().waitFor();
    for(const page of [ep,vp]){await page.goto(`${origin}/admin`);await page.waitForURL(url=>url.pathname!='/admin');await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('heading',{name:'Вход в ACTI',exact:true}).waitFor();await json(page,'/users/me','GET',undefined,401);}
    assert.deepEqual(h.runtimeErrors,[]);pass('existing administrator editor and reader cookie flows retain ACTI v2 references nullable fields login logout and forbidden administration');
    Object.assign(receipt,{status:'PASS',source_sha:platform.source_sha,scenarios:h.results,synthetic:{registry_id:registry.id,service_ids:services,source_document_id:source.id,block_id:block.id,source_version_id:extraction.version_id}});return receipt;
  } catch(error) {
    await fs.writeFile(path.join(outputDir,'failure.txt'),redact(JSON.stringify({stage,cookie_operation:h.getStage(),message:String(error.message),stack:String(error.stack||'')},null,2)));
    // Never screenshot the administration surface on failure: a one-time secret might be visible.
    if(vp)await vp.screenshot({path:path.join(outputDir,'failure-reader-viewport.png'),fullPage:false}).catch(()=>{});
    throw new Error(`Task010 failed at ${stage}; redacted diagnostics saved`);
  } finally {
    const failures=[];
    for(const id of services)try{const account=(await json(ap,'/admin/service-accounts?page_size=100')).items.find(item=>item.id===id);assert.ok(account);for(const token of account.tokens||[])if(!token.revoked_at)await json(ap,`/admin/service-accounts/${id}/tokens/${token.id}/revoke`,'POST',{reason:'Завершение синтетической проверки'},204);await json(ap,`/admin/service-accounts/${id}`,'PATCH',{name:account.name,registry_ids:account.registry_ids,operations:account.operations,state:'disabled',reason:'Завершение синтетической проверки'});const check=(await json(ap,'/admin/service-accounts?page_size=100')).items.find(item=>item.id===id);assert.equal(check.state,'disabled');assert.ok(check.tokens.every(token=>token.revoked_at));}catch{failures.push('service token revocation and account disable verification');}
    if(ap)for(const id of temporaryRegistries)try{const grants=await json(ap,`/admin/access/${id}`);await json(ap,`/admin/access/${id}`,'PUT',{entries:grants.entries.filter(entry=>!accounts.includes(entry.user_id))});assert.equal((await json(ap,`/admin/access/${id}`)).entries.some(entry=>accounts.includes(entry.user_id)),false);}catch{failures.push('temporary registry grant cleanup');}
    if(ap)for(const id of accounts)try{await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}catch{failures.push('synthetic user disable');}
    if(ap&&accounts.length)try{const users=await json(ap,'/admin/users');assert.ok([...accounts,...serviceUsers].every(id=>users.some(user=>user.id===id&&!user.active)));}catch{failures.push('synthetic and service user read back');}
    for(const client of clients)await client.dispose();for(const context of h.contexts)await context.close();
    receipt.cleanup_verified=failures.length===0;await fs.writeFile(path.join(outputDir,'scenarios.json'),JSON.stringify(h.results,null,2));await fs.writeFile(path.join(outputDir,'cleanup.json'),JSON.stringify({cleanup_verified:receipt.cleanup_verified,service_accounts_disabled:services.length,accounts_disabled:accounts.length,failures},null,2));assert.deepEqual(failures,[],'Service credential and user cleanup must succeed');
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium,request}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runTask010({...config,browser,request,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Task010 actual HTTPS browser PASS: ${receipt.scenarios.length} scenarios.`);}catch{console.error('Task010 browser FAIL; see private redacted diagnostics.');process.exitCode=1;}finally{await browser?.close();}}
