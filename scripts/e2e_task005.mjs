/** Real Task005 HTTPS content acceptance; private config also supplies fixturesDir.
 * Generate own fixtures with scripts/generate_task005_fixtures.py before running.
 * No response interception, source databases, OCR or LLM calls.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomBytes,createHash} from 'node:crypto';
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

export async function runTask005({browser,origin,admin,outputDir,fixturesDir}) {
  const h=await helpers(browser,origin,outputDir);
  const {pageFor,json,login,mutation,download,pass}=h;
  const suffix=Date.now().toString(36),code=`task005_${suffix}`,title=`Task005 ${suffix}`;
  const accounts=[],sensitive=[admin.password],temporaryRegistries=new Set();
  const fixture=name=>path.join(fixturesDir,name);
  let ap;
  try {
    ap=await pageFor();await login(ap,admin);
    const platform=await json(ap,'/admin/platform'),administrator=await json(ap,'/users/me');
    pass('administrator HTTPS login and active source',{source_sha:platform.source_sha});
    const type=await json(ap,'/document-types','POST',{code,name:title},201);
    const registry=await json(ap,'/registries','POST',{code,name:title,document_type_ids:[type.id]},201);
    const version=(await json(ap,`/document-types/${type.id}/versions`))[0];
    const schema={sections:[{key:'main',title:'Основные сведения',fields:[
      {key:'title',label:'Название Task005',type:'text',system:'title',required:true,editable:true,filterable:true,exportable:true},
      {key:'secret',label:'Скрытые реквизиты',type:'text',editable:true,read_roles:['editor'],write_roles:['editor'],exportable:true},
      {key:'public_file',label:'Публичный оригинал',type:'file',editable:true},
      {key:'private_file',label:'Скрытый оригинал',type:'file',editable:true,read_roles:['editor'],write_roles:['editor']},
    ]}]};
    await json(ap,`/form-schemas/${version.id}`,'PUT',{schema});
    await json(ap,`/form-schemas/${version.id}/publish`,'POST',{});
    const roles={};
    for(const role of ['editor','viewer']) {
      const temporary=randomBytes(24).toString('base64url'),password=randomBytes(24).toString('base64url');sensitive.push(temporary,password);
      const account=await json(ap,'/admin/users','POST',{login:`${code}_${role}`,display_name:`${title} ${role}`,password:temporary,roles:[role]},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:`${code}_${role}`,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);await page.getByLabel('Новый пароль',{exact:true}).fill(password);await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();await page.getByRole('heading',{name:'Мои реестры',exact:true}).waitFor();
      roles[role]={page,account,permissions:role==='editor'?['read','create','update','upload','download']:['read','download']};
    }
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:[{user_id:administrator.id,permissions:['read','create','update','upload','download']},...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});
    const ep=roles.editor.page,vp=roles.viewer.page;await ep.reload();await vp.reload();
    await ep.getByRole('button',{name:title,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();
    await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(type.id);
    await ep.getByLabel('Название Task005',{exact:false}).fill(`${title} public`);
    await ep.getByLabel('Скрытые реквизиты',{exact:false}).fill('СЕКРЕТНЫЙМЕТАДАННЫЙКОД');
    await ep.getByLabel('Публичный оригинал',{exact:false}).setInputFiles(fixture('public.docx'));
    await ep.getByLabel('Скрытый оригинал',{exact:false}).setInputFiles(fixture('private.docx'));
    const card=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);
    const publicFile=card.files.find(file=>file.name==='public.docx'),privateFile=card.files.find(file=>file.name==='private.docx');assert.ok(publicFile&&privateFile);
    async function upload(name) {
      await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();
      return mutation(ep,`/documents/${card.id}/files`,()=>ep.getByLabel('Добавить файл или следующую версию',{exact:false}).setInputFiles(fixture(name)),'POST',201);
    }
    for(const name of ['public.pdf','public.doc','short.doc','updated.pdf','broken.pdf','blank.pdf','encrypted.pdf']) await upload(name);
    const expected={'public.docx':'completed','private.docx':'completed','public.pdf':'completed','public.doc':'completed','short.doc':'unsupported','updated.pdf':'completed','broken.pdf':'failed','blank.pdf':'no_text','encrypted.pdf':'encrypted'};
    async function waitContent(page,documentId,desired,timeout=120000) {
      const deadline=Date.now()+timeout;let attempts=0,last;
      while(Date.now()<deadline) {
        last=await json(page,`/documents/${documentId}/content`);
        if(Object.entries(desired).every(([name,state])=>last.items.some(item=>item.name===name&&item.state===state)))return last;
        const failed=last.items.find(item=>desired[item.name]==='completed'&&['failed','unsupported','encrypted','no_text'].includes(item.state));
        if(failed){
          await fs.writeFile(path.join(outputDir,'pipeline-failure.json'),JSON.stringify({document_id:documentId,expected:desired,observed:last.items.map(item=>({id:item.id,file_id:item.file_id,name:item.name,state:item.state,error_code:item.error_code,parser:item.parser,parser_version:item.parser_version,attempts:item.attempts,block_count:item.block_count}))},null,2));
          throw new Error(`Expected supported synthetic document ${failed.name} failed extraction: ${failed.state}/${failed.error_code||'no_code'}`);
        }
        if(++attempts%10===0)console.log('Waiting for real background extraction and PostgreSQL indexing');
        await delay(1000);
      }
      await fs.writeFile(path.join(outputDir,'pipeline-timeout.json'),JSON.stringify({document_id:documentId,expected:desired,observed:last?.items.map(item=>({name:item.name,state:item.state,error_code:item.error_code,attempts:item.attempts}))},null,2));
      throw new Error('Actual extraction did not reach required states before timeout');
    }
    const content=await waitContent(ep,card.id,expected);
    const docx=content.items.find(item=>item.file_id===publicFile.id),hiddenExtraction=content.items.find(item=>item.file_id===privateFile.id);
    assert.ok(docx.blocks.some(block=>block.kind==='table'&&Array.isArray(block.table)));
    assert.ok(docx.blocks.some(block=>block.kind==='heading'));
    assert.ok(content.items.find(item=>item.name==='public.doc').blocks.some(block=>block.text.includes('благоустройстве')));
    pass('editor UI uploads DOCX Russian digital PDF and native DOC with real asynchronous structured extraction');
    const beforeIds=docx.blocks.map(block=>block.id);
    const retry=await json(ep,`/files/${publicFile.id}/extract`,'POST',{},202);assert.equal(retry.id,docx.id);
    const after=(await json(ep,`/documents/${card.id}/content`)).items.find(item=>item.id===docx.id);assert.deepEqual(after.blocks.map(block=>block.id),beforeIds);
    pass('completed extraction retry retains fingerprint and stable blocks without duplication');

    await ep.getByRole('button',{name:'Содержание',exact:true}).click();
    const brokenPanel=ep.locator('details.extraction').filter({has:ep.locator('summary').filter({hasText:'broken.pdf'})});
    if(!(await brokenPanel.evaluate(element=>element.open)))await brokenPanel.locator('summary').click();
    if(await brokenPanel.getByRole('button',{name:'Повторить извлечение',exact:true}).count()) {
      const repeated=await mutation(ep,`/files/${content.items.find(item=>item.name==='broken.pdf').file_id}/extract`,()=>brokenPanel.getByRole('button',{name:'Повторить извлечение',exact:true}).click(),'POST',202);
      assert.equal(repeated.id,content.items.find(item=>item.name==='broken.pdf').id);await waitContent(ep,card.id,{'broken.pdf':'failed'});
    }
    assert.equal((await json(ep,`/documents/${card.id}`)).id,card.id);
    const shortDoc=content.items.find(item=>item.name==='short.doc');assert.equal(shortDoc.error_code,'legacy_doc_short_stream');assert.ok(shortDoc.warnings.includes('legacy_doc_short_stream_unsupported'));
    pass('corrupt encrypted textless and valid short legacy DOC retain explicit states originals and accessible card');

    const closed=await json(ep,'/documents','POST',{registry_id:registry.id,document_type_id:type.id,schema_version_id:version.id,values:{title:`${title} closed`},files:[{field_key:'public_file',name:'closed.docx',mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',content_base64:(await fs.readFile(fixture('public.docx'))).toString('base64')}]},201);
    await json(ap,`/documents/${closed.id}/acl`,'PUT',{entries:[{user_id:administrator.id,permissions:['read','download']},{user_id:roles.editor.account.id,permissions:['read','update','download']}]});
    await waitContent(ep,closed.id,{'closed.docx':'completed'});
    await json(vp,`/documents/${closed.id}/content`,'GET',undefined,404);
    await json(vp,`/content/extractions/${hiddenExtraction.id}/blocks`,'GET',undefined,404);
    await json(vp,`/files/${privateFile.id}/download`,'GET',undefined,404);
    await json(vp,`/files/${publicFile.id}/extract`,'POST',{},403);
    await json(ep,'/admin/content/errors','GET',undefined,403);
    const readerContent=await json(vp,`/documents/${card.id}/content?mode=viewer`);
    assert.equal(readerContent.items.some(item=>item.file_id===privateFile.id),false);
    async function search(page,text,fuzzy=true){return json(page,`/content/search?registry_id=${registry.id}&text=${encodeURIComponent(text)}&fuzzy=${fuzzy}&mode=viewer`);}
    assert.equal((await search(vp,'СЕКРЕТНЫЙМЕТАДАННЫЙКОД')).total,0);
    await json(vp,`/content/search?registry_id=${registry.id}&text=archive&filters=${encodeURIComponent(JSON.stringify({secret:'СЕКРЕТНЫЙМЕТАДАННЫЙКОД'}))}`,'GET',undefined,422);
    assert.equal((await search(vp,'ЗАКРЫТЫЙФАЙЛМАРКЕР')).total,0);
    const phrase=await search(vp,'городском благоустройстве');assert.equal(phrase.total,1);assert.equal(phrase.items[0].document.id,card.id);
    assert.equal(JSON.stringify(phrase).includes(closed.id),false);
    assert.ok(phrase.items[0].matches.some(match=>match.source==='file'&&match.block_id&&match.version));
    assert.equal((await search(vp,'благоустройстов')).total,1);
    pass('ACL precedes phrase typo snippets counts hidden metadata hidden file and closed document search');
    const currentGrants=await json(ap,`/admin/access/${registry.id}`);
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:currentGrants.entries.map(entry=>entry.user_id===roles.viewer.account.id?{...entry,permissions:['read']}:entry)});
    await json(vp,`/documents/${card.id}/content`,'GET',undefined,403);
    await json(vp,`/files/${publicFile.id}/download`,'GET',undefined,403);
    assert.equal((await search(vp,'городском благоустройстве')).total,0);
    assert.equal((await json(vp,`/documents/${card.id}`)).id,card.id);
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:currentGrants.entries});
    assert.equal((await search(vp,'городском благоустройстве')).total,1);
    pass('live download permission changes immediately remove and restore source search without stale snippets');

    await vp.getByRole('button',{name:title,exact:true}).click();await vp.getByLabel('Поиск по содержанию',{exact:true}).fill('городском благоустройстве');await vp.getByLabel('Учитывать опечатки',{exact:true}).check();
    await vp.getByRole('button',{name:'Найти',exact:true}).click();await vp.getByText('Найдено: 1',{exact:true}).waitFor();
    await vp.locator('.content-matches').first().waitFor();
    const csvPath=await download(vp,'Экспорт CSV','content-search-reader.csv');await download(vp,'Экспорт XLSX','content-search-reader.xlsx');
    const csv=await fs.readFile(csvPath,'utf8');assert.ok(csv.includes(`${title} public`));assert.equal(csv.includes(`${title} closed`),false);assert.equal(csv.includes('СЕКРЕТНЫЙМЕТАДАННЫЙКОД'),false);
    await vp.getByRole('button',{name:'Открыть фрагмент источника',exact:true}).first().click();
    await vp.getByRole('heading',{name:'Содержание и источники',exact:true}).waitFor();await vp.locator('.content-block.selected').waitFor();
    await vp.screenshot({path:path.join(outputDir,'reader-source-block.png'),fullPage:true});
    await vp.screenshot({path:path.join(outputDir,'reader-source-viewport.png'),fullPage:false});
    const selectedSource=vp.locator('details.extraction').filter({has:vp.locator('.content-block.selected')});
    const sourceName=(await selectedSource.locator('summary').innerText()).split(' · версия ')[0].trim();
    assert.ok(['public.docx','public.doc','public.pdf','updated.pdf'].includes(sourceName));
    const downloadPromise=vp.waitForEvent('download');await selectedSource.getByRole('button',{name:/^Скачать оригинал версии /}).click();const original=await downloadPromise;
    assert.equal(original.suggestedFilename(),sourceName);
    const downloaded=path.join(outputDir,'source-original-download');await original.saveAs(downloaded);
    const sha=data=>createHash('sha256').update(data).digest('hex');
    assert.equal(sha(await fs.readFile(downloaded)),sha(await fs.readFile(fixture(sourceName))));
    pass('reader UI phrase search restricted exports source version block deep link and unchanged original download');
    const pdfPhrase=await search(vp,'организации дорожного движения');assert.equal(pdfPhrase.total,1);assert.ok(pdfPhrase.items[0].matches.some(match=>match.label==='public.pdf'));
    const updated=await search(vp,'КВАНТОВЫЙМАРШРУТ');assert.equal(updated.total,1);assert.ok(updated.items[0].matches.some(match=>match.label==='updated.pdf'&&match.version>1));
    await vp.goto(`${origin}/view?document=${card.id}&tab=content&file=${publicFile.id}&block=${docx.blocks[0].id}&ordinal=0#block-${docx.blocks[0].id}`);
    await vp.locator('.content-block.selected').waitFor();assert.equal(await vp.getByRole('button',{name:'Повторить извлечение',exact:true}).count(),0);
    pass('old and new file versions remain searchable and source links reopen exact permitted version');
    await ap.getByRole('navigation').getByRole('button',{name:'Обработка содержания',exact:true}).click();await ap.getByRole('heading',{name:'Обработка содержания',exact:true}).waitFor();
    const errors=await json(ap,'/admin/content/errors');assert.ok(errors.items.some(item=>item.document_id===card.id&&item.name==='broken.pdf'));
    pass('administrator sees authorized processing errors through actual operator UI');

    const legacyType=(await json(ap,'/document-types')).find(value=>value.code==='acti-v2'),legacyRegistry=(await json(ap,'/registries')).find(value=>value.document_type_ids.includes(legacyType.id));
    const previous=await json(ap,`/admin/access/${legacyRegistry.id}`);await json(ap,`/admin/access/${legacyRegistry.id}`,'PUT',{entries:[...previous.entries,...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});temporaryRegistries.add(legacyRegistry.id);
    await ep.reload();await ep.getByRole('button',{name:legacyRegistry.name,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(legacyType.id);await ep.getByLabel('Наименование документа',{exact:false}).fill(`${title} legacy smoke`);await ep.getByLabel('Регистрационный номер',{exact:true}).fill('000005');await ep.getByRole('combobox',{name:'Тип документа ACTI v2',exact:true}).selectOption({label:'Постановление'});await ep.getByLabel('Номер тома',{exact:true}).fill('1-2');
    const legacyCard=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);assert.equal(legacyCard.reg_number,'000005');assert.equal(legacyCard.values.should_publish??null,null);assert.equal(legacyCard.values.volume_number,'1-2');assert.match(legacyCard.values.type_id,/^[0-9a-f-]{36}$/i);
    await vp.goto(`${origin}/view?document=${legacyCard.id}`);await vp.getByText('000005',{exact:true}).first().waitFor();await vp.getByText('Постановление',{exact:true}).first().waitFor();await vp.getByText('1-2',{exact:true}).first().waitFor();
    pass('ACTI v2 generated card legacy number and nullable publication remain compatible');
    for(const page of [ep,vp]){await page.goto(`${origin}/admin`);await page.waitForURL(url=>url.pathname!='/admin');await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('heading',{name:'Вход в ACTI',exact:true}).waitFor();await json(page,'/users/me','GET',undefined,401);}
    assert.deepEqual(h.runtimeErrors,[]);pass('editor reader forbidden administration and actual logout no application errors');
    return {status:'PASS',source_sha:platform.source_sha,scenarios:h.results,synthetic:{registry_id:registry.id,document_id:card.id,closed_document_id:closed.id},states:content.items.map(item=>({name:item.name,state:item.state,parser:item.parser,version:item.version}))};
  } catch(error) {
    const failure={stage:h.getStage(),message:String(error.message),stack:String(error.stack||'')};
    await fs.writeFile(path.join(outputDir,'failure.txt'),sensitive.reduce((message,value)=>message.split(value).join('[redacted]'),JSON.stringify(failure,null,2)));
    console.error(`Task005 failed operation: ${failure.stage}`);
    let screenshotIndex=0;
    for(const context of h.contexts)for(const page of context.pages()) {
      await page.screenshot({path:path.join(outputDir,`failure-page-${screenshotIndex++}.png`),fullPage:true}).catch(()=>{});
    }
    throw error;
  } finally {
    const failures=[];
    if(ap)for(const registryId of temporaryRegistries)try{const current=await json(ap,`/admin/access/${registryId}`);await json(ap,`/admin/access/${registryId}`,'PUT',{entries:current.entries.filter(value=>!accounts.includes(value.user_id))});}catch{failures.push('temporary registry grants');}
    if(ap)for(const id of accounts)try{await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}catch{failures.push('synthetic account');}
    for(const context of h.contexts)await context.close();await fs.writeFile(path.join(outputDir,'scenarios.json'),JSON.stringify(h.results,null,2));assert.deepEqual(failures,[],'Synthetic cleanup must succeed');
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runTask005({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Task005 actual HTTPS browser PASS: ${receipt.scenarios.length} scenarios.`);}catch(error){console.error(`Task005 browser FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
