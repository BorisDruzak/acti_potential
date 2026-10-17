/** Real HTTPS acceptance for searchable people fields. All mutations use owned
 * synthetic fixtures. Config matches e2e_document_workspace.mjs; no response mocks.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomBytes} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

export async function runPeopleSelection({browser, origin, admin, outputDir, expectedSha}) {
  assert.equal(new URL(origin).protocol, 'https:', 'Strict HTTPS is required');
  await fs.mkdir(outputDir, {recursive:true});
  const contexts=[], accounts=[], registries=[], checks=[], errors=[], requests=[];
  const people=[['executors','executor_id','Исполнитель'], ['responsible_executors','responsible_executor_id','Ответственный исполнитель'], ['signers','signers','Подписанты'], ['approvers','approvers','Согласующие']];
  let ap, ep, stage='login';
  async function pageFor() {
    const context=await browser.newContext({viewport:{width:1600,height:1050},ignoreHTTPSErrors:false});
    contexts.push(context);
    const page=await context.newPage();page.setDefaultTimeout(20000);
    page.on('pageerror', error=>errors.push(error.name+': '+error.message));
    return page;
  }
  async function json(page, endpoint, method='GET', data, expected=200) {
    const result=await page.evaluate(async ({endpoint,method,data})=>{
      const me=method==='GET'?null:await (await fetch('/api/v1/users/me')).json();
      const response=await fetch('/api/v1'+endpoint, {method,headers:{Accept:'application/json',...(data===undefined?{}:{'Content-Type':'application/json'}),...(me?{'X-CSRF-Token':me.csrf_token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
      return {status:response.status,data:await response.json().catch(()=>null)};
    }, {endpoint,method,data});
    const problem=result.data?.error;
    const detail=problem&&typeof problem==='object'?[problem.code,problem.message].filter(value=>typeof value==='string').join(': '):'';
    assert.equal(result.status, expected, `${method} ${endpoint.split('?')[0]}${detail?' — '+detail:''}`);
    return result.data;
  }
  async function login(page, credentials) {
    await page.goto(origin+'/work');
    await page.getByLabel('Логин',{exact:true}).fill(credentials.login);
    await page.getByLabel('Пароль',{exact:true}).fill(credentials.password);
    await page.getByRole('button',{name:'Войти в рабочее пространство',exact:true}).click();
    await page.getByRole('button',{name:'Выйти',exact:true}).waitFor();
  }
  async function dismissNav(page) {
    await page.getByRole('group',{name:'Вид и сортировка документов',exact:true}).waitFor();
    const close=page.getByRole('button',{name:'Скрыть панель',exact:true});
    if(await close.isVisible())await close.click();
    else await page.getByRole('main').click({position:{x:1000,y:10}});
    await page.waitForFunction(()=>document.getElementById('main-navigation')?.inert);
  }
  const pass=name=>{checks.push(name);console.log('PASS '+name);};
  const fieldMap=schema=>{
    const result=[];
    const add=fields=>{for(const field of fields||[]){result.push(field);add(field.children);}};
    for(const section of schema.sections||[])add(section.fields);
    return result;
  };
  try {
    ap=await pageFor();await login(ap,admin);
    assert.equal((await json(ap,'/admin/platform')).source_sha,expectedSha);
    await json(ap,'/health/ready');
    pass('Strict HTTPS, authenticated API and expected deployed source revision');
    stage='builtin configuration presentation';
    const builtin=(await json(ap,'/document-types')).find(type=>type.code==='acti-v2');
    assert(builtin,'Existing builtin document type is available');
    assert(!/ACTI\s*v\s*2/i.test(builtin.name));
    const builtinVersion=(await json(ap,`/document-types/${builtin.id}/versions`)).filter(version=>version.state==='published').sort((a,b)=>b.version-a.version)[0];
    assert(builtinVersion);
    const builtinFields=fieldMap(builtinVersion.schema);
    for(const [code,key] of people){const field=builtinFields.find(field=>field.key===key||field.reference_code===code);assert(field,code);assert.equal(field.type,'multi_reference',code);}
    for(const field of builtinFields)assert(!/ACTI\s*v\s*2/i.test(field.label+' '+(field.description||'')),field.key);
    pass('Existing builtin form uses multi-reference people fields and clean configuration labels');

    stage='owned configuration';
    const code='people_'+Date.now().toString(36), me=await json(ap,'/users/me');
    const type=await json(ap,'/document-types','POST',{code,name:'Участники ACTI v2 '+code},201);
    assert(!/ACTI\s*v\s*2/i.test(type.name));
    const registry=await json(ap,'/registries','POST',{code,name:'Проверка участников '+code,document_type_ids:[type.id]},201);registries.push(registry);
    const foreignRegistry=await json(ap,'/registries','POST',{code:code+'_foreign',name:'Проверка границы участников '+code,document_type_ids:[]},201);registries.push(foreignRegistry);
    const choices={};
    for(const [referenceCode,key,label] of people) {
      const set=await json(ap,'/references','POST',{code:referenceCode,name:label+' ACTI v2',registry_id:registry.id},201);
      assert(!/ACTI\s*v\s*2/i.test(set.name));
      const items=[];
      for(const [index,name] of ['Иван Петров','Мария Соколова','Алексей Орлов','Недоступный участник','Виктор Лебедев','Ольга Смирнова','Сергей Волков','Елена Морозова'].entries())items.push(await json(ap,`/references/${set.id}/items`,'POST',{label:name,sort_order:index,active:index!==3},201));
      choices[key]={set,items,label};
    }
    const otherChoices={};
    for(const [referenceCode,key,label] of people) {
      const set=await json(ap,'/references','POST',{code:referenceCode,name:label+' другого реестра',registry_id:foreignRegistry.id},201);
      const items=[];
      for(const suffix of ['Первый','Второй'])items.push(await json(ap,`/references/${set.id}/items`,'POST',{label:`Другой реестр — ${label} ${suffix}`},201));
      otherChoices[key]={set,items};
    }
    const foreign=otherChoices.executor_id.items[0];
    await json(ap,`/registries/${foreignRegistry.id}`,'PATCH',{document_type_ids:[type.id]});
    const version=(await json(ap,`/document-types/${type.id}/versions`))[0];
    const schema={sections:[{key:'main',title:'Основные сведения ACTI v2',fields:[
      {key:'caption',label:'Наименование документа ACTI v2',system:'title',type:'text',editable:true,required:true,searchable:true},
      ...people.map(([referenceCode,key,label])=>({key,label:label+' ACTI v2',type:'multi_reference',reference_code:referenceCode,editable:true,required:key==='executor_id'})),
    ]}]};
    await json(ap,`/form-schemas/${version.id}`,'PUT',{schema});
    const published=await json(ap,`/form-schemas/${version.id}/publish`,'POST',{});
    assert(!/ACTI\s*v\s*2/i.test(published.schema.sections[0].title));
    const roles={};
    for(const role of ['editor','viewer']) {
      const temporary=randomBytes(24).toString('base64url'), password=randomBytes(24).toString('base64url');
      const account=await json(ap,'/admin/users','POST',{login:code+'_'+role,display_name:'Проверка участников '+role,password:temporary,roles:[role]},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:code+'_'+role,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);
      await page.getByLabel('Новый пароль',{exact:true}).fill(password);
      await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();
      await page.getByRole('heading',{name:'Документы',exact:true,level:1}).waitFor();
      roles[role]={page,account};
    }
    for(const owned of registries)await json(ap,`/admin/access/${owned.id}`,'PUT',{entries:[{user_id:me.id,permissions:['read','create','update','download']},{user_id:roles.editor.account.id,permissions:['read','create','update','download']},{user_id:roles.viewer.account.id,permissions:['read']} ]});
    const foreignCard=await json(ap,'/documents','POST',{registry_id:foreignRegistry.id,document_type_id:type.id,schema_version_id:version.id,card_state:'published',publication_targets:['internal','shared'],values:{caption:'Другой реестр '+code,...Object.fromEntries(people.map(([,key])=>[key,[otherChoices[key].items[0].id]]))}},201);
    ep=roles.editor.page;
    ep.on('request',request=>{const url=new URL(request.url());if(/^\/api\/v1\/references\/[^/]+\/items$/.test(url.pathname))requests.push(url.pathname);});
    await ep.goto(`${origin}/work?registry_id=${registry.id}&document_type_id=${type.id}`);await dismissNav(ep);
    const toolbar=ep.getByRole('group',{name:'Вид и сортировка документов',exact:true});
    await toolbar.getByRole('button',{name:'Создать документ',exact:true}).click();
    const create=ep.locator('.document-create');await create.waitFor();
    await create.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(type.id);
    await create.getByRole('textbox',{name:/^Наименование документа/}).fill('Поиск участников '+code);
    await create.getByRole('combobox',{name:'Состояние карточки',exact:true}).selectOption('published');
    await create.getByLabel('Общая база',{exact:true}).check();
    assert.equal(await ep.getByRole('dialog').count(),0,'Creation remains inside the card pane');
    pass('Inline creation renders cleaned schema labels and scoped reference lists');

    stage='normal click and Cyrillic search';
    for(const [,key,label] of people) {
      const group=create.getByRole('group',{name:label,exact:true}), search=create.getByRole('searchbox',{name:'Поиск: '+label,exact:true});
      await group.getByRole('checkbox',{name:'Иван Петров',exact:true}).waitFor();
      await group.getByRole('checkbox',{name:'Недоступный участник (неактивно)',exact:true}).isDisabled().then(value=>assert(value));
      const geometry=await group.evaluate(el=>{
        const bounds=el.getBoundingClientRect(), rows=Array.from(el.querySelectorAll('.lookup-option')).map(row=>row.getBoundingClientRect());
        return {rowHeights:rows.map(row=>row.height),visible:rows.filter(row=>row.top>=bounds.top+el.clientTop-1&&row.bottom<=bounds.top+el.clientTop+el.clientHeight+1).length,scroll:el.scrollHeight>el.clientHeight};
      });
      assert(geometry.rowHeights.every(height=>height===32),'Desktop rows are flat and 32px tall');
      assert.equal(geometry.visible,5,'Exactly five full options are visible');assert(geometry.scroll,'Further options scroll inside the list');
      const last=group.getByRole('checkbox',{name:'Елена Морозова',exact:true});await last.scrollIntoViewIfNeeded();await last.click();assert.equal(await last.isChecked(),true);await last.click();
      await group.evaluate(el=>el.scrollTop=0);
      const before=requests.filter(endpoint=>endpoint.includes(choices[key].set.id)).length;
      await search.fill('ИВАН');await group.getByRole('checkbox',{name:'Иван Петров',exact:true}).click();
      assert.equal(await group.getByRole('checkbox',{name:'Алексей Орлов',exact:true}).count(),0);
      await search.fill('сОкОлОвА');await group.getByRole('checkbox',{name:'Мария Соколова',exact:true}).click();
      assert.equal(await group.getByRole('checkbox',{name:'Иван Петров',exact:true}).isChecked(),true,'Selected names remain visible when filtered');
      await search.fill('ничего не найдено');
      assert.equal(await group.getByRole('checkbox').count(),2);
      await search.fill('');
      const maria=group.getByRole('checkbox',{name:'Мария Соколова',exact:true});
      await maria.click();assert.equal(await maria.isChecked(),false);await maria.click();
      await maria.focus();await ep.keyboard.press('Space');assert.equal(await maria.isChecked(),false);await ep.keyboard.press('Space');assert.equal(await maria.isChecked(),true);
      assert.equal(requests.filter(endpoint=>endpoint.includes(choices[key].set.id)).length,before,'Local reference search must not refetch');
    }
    await create.getByRole('group',{name:'Исполнитель',exact:true}).locator('..').locator('..').screenshot({path:path.join(outputDir,'people-five-visible-options.png')});
    await ep.screenshot({path:path.join(outputDir,'people-multiple-selection.png'),fullPage:true,mask:[ep.locator('.account')]});
    pass('All four people fields support Cyrillic case-insensitive local search, normal multi-click and Space; filters retain selection without refetch');
    stage='save and reopen';
    const creating=ep.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/documents'&&response.request().method()==='POST');
    await create.getByRole('button',{name:'Создать документ',exact:true}).click();
    const response=await creating;assert.equal(response.status(),201);let card=await response.json();
    for(const [,key] of people)assert.deepEqual(card.values[key],choices[key].items.slice(0,2).map(item=>item.id));
    await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).waitFor();
    await ep.reload();await dismissNav(ep);
    const detail=ep.getByRole('region',{name:'Содержимое карточки',exact:true});
    await detail.getByRole('button',{name:'Редактировать карточку',exact:true}).waitFor();
    for(const [,key] of people)assert.deepEqual((await json(ep,`/documents/${card.id}`)).values[key],choices[key].items.slice(0,2).map(item=>item.id));
    assert.equal(await detail.locator('.lookup-control').count(),0,'Saved people fields are read-only until edit');
    assert.equal(await detail.getByRole('searchbox').count(),0);
    await detail.locator('.field-value').filter({hasText:'Иван Петров, Мария Соколова'}).first().waitFor();
    assert.equal(await detail.locator('.field-value').filter({hasText:'Иван Петров, Мария Соколова'}).count(),4);
    pass('Real create/save and reload preserve arrays and human labels; card view has no mutable people controls');

    stage='inactive selection';
    const executor=choices.executor_id;
    await json(ap,`/references/${executor.set.id}/items/${executor.items[0].id}`,'PATCH',{active:false});
    await detail.getByRole('button',{name:'Редактировать карточку',exact:true}).click();
    const executorGroup=detail.getByRole('group',{name:'Исполнитель',exact:true});
    const inactive=executorGroup.getByRole('checkbox',{name:'Иван Петров (неактивно)',exact:true});
    await inactive.waitFor();assert.equal(await inactive.isChecked(),true);assert.equal(await inactive.isEnabled(),true);
    await inactive.click();assert.equal(await inactive.isChecked(),false);assert.equal(await inactive.isDisabled(),true);
    const saving=ep.waitForResponse(response=>new URL(response.url()).pathname===`/api/v1/documents/${card.id}`&&response.request().method()==='PUT');
    await detail.getByRole('button',{name:'Сохранить карточку',exact:true}).click();
    const saved=await saving;assert.equal(saved.status(),200);card=await saved.json();assert.deepEqual(card.values.executor_id,[executor.items[1].id]);
    await detail.getByRole('button',{name:'Редактировать карточку',exact:true}).waitFor();
    await detail.getByRole('button',{name:'История изменений',exact:true}).click();
    const first=detail.getByText(/^Редакция 1 ·/).locator('..');await first.locator('summary').click();
    await first.locator('.field-value').filter({hasText:'Иван Петров, Мария Соколова'}).first().waitFor();
    assert.equal(await first.locator('.lookup-control').count(),0);
    pass('Previously selected inactive person can be removed, cannot be newly selected, and historical multiple labels remain readable');

    stage='scope and transaction validation';
    const before=await json(ep,`/documents/${card.id}`), historyBefore=await json(ep,`/documents/${card.id}/history`);
    await json(ep,`/documents/${card.id}`,'PUT',{revision:before.revision,values:{executor_id:[foreign.id]}},422);
    const after=await json(ep,`/documents/${card.id}`);
    assert.equal(after.revision,before.revision);assert.deepEqual(after.values,before.values);
    assert.equal((await json(ep,`/documents/${card.id}/history`)).length,historyBefore.length);
    pass('Foreign registry reference is rejected without changing values, revision or history');

    stage='all-registry selection';
    for(const ownedCard of [card,foreignCard]) {
      for(let attempt=0;attempt<60;attempt++) {
        if((await json(ep,`/documents/${ownedCard.id}/search-index`)).ready)break;
        if(attempt===59)assert.fail('Owned registry card did not index');await delay(300);
      }
    }
    await ep.getByRole('button',{name:'Карточка',exact:true}).click();
    await ep.getByRole('radio',{name:'Вместе',exact:true}).click();
    await ep.getByRole('combobox',{name:'Реестр',exact:true}).selectOption('');
    await ep.getByRole('combobox',{name:'Тип',exact:true}).selectOption(type.id);
    await ep.getByRole('button',{name:'Найти',exact:true}).click();
    for(const [selected,books] of [[foreignCard,otherChoices],[card,choices]]) {
      await ep.locator(`tr[data-document-id="${selected.id}"]`).click();
      await detail.getByRole('heading',{name:selected.title,exact:true,level:1}).waitFor();
      await detail.getByRole('button',{name:'Редактировать карточку',exact:true}).click();
      for(const [,key,label] of people) {
        const group=detail.getByRole('group',{name:label,exact:true});
        await group.getByRole('checkbox',{name:books[key].items[1].label,exact:true}).waitFor();
        const unexpected=books===choices?otherChoices[key].items[1].label:'Мария Соколова';
        assert.equal(await group.getByRole('checkbox',{name:unexpected,exact:true}).count(),0,'Other registry values must not leak into choices');
      }
      assert.equal(await ep.getByRole('combobox',{name:'Реестр',exact:true}).inputValue(),'','Result selection keeps all-registry search');
      await detail.getByRole('button',{name:'Отмена изменений',exact:true}).click();
    }
    pass('All-registry search switches all four scoped reference lists with the selected document while preserving the all-registry filter');

    stage='reader and small screen';
    const vp=roles.viewer.page;
    await vp.goto(`${origin}/view?registry_id=${registry.id}&document=${card.id}`);await dismissNav(vp);
    const reader=vp.getByRole('region',{name:'Содержимое карточки',exact:true});
    await reader.locator('.field-value').filter({hasText:'Мария Соколова'}).first().waitFor();
    assert.equal(await reader.locator('.lookup-control').count(),0);assert.equal(await reader.getByRole('searchbox').count(),0);
    assert.equal(await vp.getByRole('button',{name:'Редактировать карточку',exact:true}).count(),0);
    await json(vp,`/documents/${card.id}`,'PUT',{revision:card.revision,values:{executor_id:[]}},403);
    await ep.getByRole('button',{name:'Карточка',exact:true}).click();
    await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).click();
    await ep.setViewportSize({width:540,height:900});
    await ep.getByRole('radio',{name:'Карточка',exact:true}).click();
    const mobile=detail.getByRole('searchbox',{name:'Поиск: Ответственный исполнитель',exact:true});await mobile.scrollIntoViewIfNeeded();
    await mobile.fill('ИВАН');
    const bounds=await mobile.boundingBox();assert(bounds && bounds.x>=0 && bounds.x+bounds.width<=541,'People search fits small viewport');
    const option=detail.getByRole('group',{name:'Ответственный исполнитель',exact:true}).getByRole('checkbox',{name:'Иван Петров',exact:true});
    assert.equal(await option.isChecked(),true);
    await ep.screenshot({path:path.join(outputDir,'people-mobile-search.png'),fullPage:true,mask:[ep.locator('.account')]});
    // Discard only this owned synthetic draft before closing its context.
    await ep.getByRole('button',{name:'Отмена изменений',exact:true}).click();
    await vp.screenshot({path:path.join(outputDir,'people-reader.png'),fullPage:true,mask:[vp.locator('.account')]});
    assert.deepEqual(errors,[]);
    pass('Reader sees resolved labels without edit/search controls; write API remains forbidden; mobile search fits viewport; no runtime errors');
    return {status:'PASS',source_sha:expectedSha,checks,errors};
  } catch(error) {
    await fs.writeFile(path.join(outputDir,'failure.txt'),stage+'\n'+String(error.stack||error));throw error;
  } finally {
    const cleanup=[];
    if(ap) {
      for(const registry of registries)try{await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:[]});}catch(error){cleanup.push(error.message);}
      for(const id of accounts)try{await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}catch(error){cleanup.push(error.message);}
    }
    for(const [index,context] of contexts.entries()) {
      for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`last-${index}.png`),fullPage:true,mask:[page.locator('.account')]}).catch(()=>{});
      await context.close();
    }
    await fs.writeFile(path.join(outputDir,'checks.json'),JSON.stringify({checks,stage,errors,cleanup},null,2));
    assert.deepEqual(cleanup,[],'Owned fixture access must be revoked and temporary accounts disabled');
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  let browser;
  try {
    const config=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
    const admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));
    const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');
    browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});
    const receipt=await runPeopleSelection({...config,browser,admin});
    await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));
    console.log(`People selection HTTPS PASS: ${receipt.checks.length} scenarios.`);
  } catch(error) {
    console.error(`People selection FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;
  } finally {await browser?.close();}
}
