/** Real-browser acceptance against an explicitly supplied disposable HTTPS environment.
 * Private JSON: origin, outputDir, users:{editor,second,viewer}:{login,password},
 * registry_id, other_registry_id, type_id; optional expectedSha, playwrightModule,
 * executablePath, values (required fixture defaults), references:
 * [{field_key,reference_set_id,items:[{id,label,active}],query,expected_id}].
 * Uses real sessions/API/UI without response mocks. Creates synthetic cards only;
 * retains cards, number receipts and claims as acceptance evidence. Never use on production.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

export async function runSearchNumberWorkflow(config) {
  const {browser,origin,outputDir,users,registry_id:registryId,other_registry_id:otherRegistryId,type_id:typeId}=config;
  assert.equal(new URL(origin).protocol,'https:','Strict HTTPS is required');
  assert(users?.editor&&users?.second&&users?.viewer,'Three distinct fixture accounts are required');
  assert(registryId&&otherRegistryId&&typeId&&registryId!==otherRegistryId,'Two disposable registries and one published type are required');
  await fs.mkdir(outputDir,{recursive:true});
  const contexts=[],pages=[],checks=[],created=[],errors=[];
  const marker='search_number_'+Date.now().toString(36)+'_'+randomUUID().slice(0,8);
  let stage='login',version,titleField,numberField,lookupField,reference,editor,second,viewer;
  const pass=name=>{checks.push(name);console.log('PASS '+name);};
  const flat=schema=>schema.sections.flatMap(section=>section.fields);
  const escaped=text=>text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const named=(pane,field)=>pane.getByLabel(new RegExp('^'+escaped(field.label)+'(?:\\s+\\*)?$'));
  async function eventually(predicate,message,timeout=20000) {
    const end=Date.now()+timeout;
    do {if(await predicate())return;await delay(150);}while(Date.now()<end);
    assert.fail(message);
  }
  async function apiResult(page,endpoint,method='GET',data) {
    return page.evaluate(async({endpoint,method,data})=>{
      const me=method==='GET'?null:await(await fetch('/api/v1/users/me')).json();
      const response=await fetch('/api/v1'+endpoint,{method,credentials:'same-origin',headers:{Accept:'application/json',
        ...(data===undefined?{}:{'Content-Type':'application/json'}),...(me?{'X-CSRF-Token':me.csrf_token}:{})},
        ...(data===undefined?{}:{body:JSON.stringify(data)})});
      return {status:response.status,data:await response.json().catch(()=>null)};
    },{endpoint,method,data});
  }
  async function api(page,endpoint,method='GET',data,expected=200) {
    const result=await apiResult(page,endpoint,method,data);
    assert.equal(result.status,expected,`${method} ${endpoint.split('?')[0]} returned ${result.status} (${result.data?.error?.code||'no error code'})`);
    return result.data;
  }
  async function session(credentials) {
    const context=await browser.newContext({viewport:{width:1650,height:1050},ignoreHTTPSErrors:false});contexts.push(context);
    const page=await context.newPage();pages.push(page);page.setDefaultTimeout(20000);
    page.on('pageerror',error=>errors.push(error.name+': '+error.message));
    await page.goto(origin+'/work');
    await page.getByLabel('Логин',{exact:true}).fill(credentials.login);
    await page.getByLabel('Пароль',{exact:true}).fill(credentials.password);
    await page.getByRole('button',{name:'Войти в рабочее пространство',exact:true}).click();
    await page.getByRole('button',{name:'Выйти',exact:true}).waitFor();return page;
  }
  async function dismissNav(page) {
    await page.getByRole('group',{name:'Вид и сортировка документов',exact:true}).waitFor();
    const close=page.getByRole('button',{name:'Скрыть панель',exact:true});
    if(await close.isVisible())await close.click();
    else await page.getByRole('main').click({position:{x:900,y:8}});
  }
  async function route(page,registry=registryId,extra='',readOnly=false) {
    await page.goto(`${origin}/${readOnly?'view':'work'}?registry_id=${registry}${extra}`);await dismissNav(page);
  }
  async function screenshot(page,name) {
    await page.screenshot({path:path.join(outputDir,name+'.png'),fullPage:true,mask:[page.locator('.account')]});
  }
  const searchResponse=page=>page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/search'&&response.request().method()==='POST');
  async function search(page) {
    const response=searchResponse(page);await page.locator('.search-form').getByRole('button',{name:'Найти',exact:true}).click();
    const result=await response;assert.equal(result.status(),200);await eventually(async()=>/^Время поиска:/.test(await page.locator('.search-status').innerText()),'Search did not complete');
    return result.json();
  }
  const cardValues=(title,number)=>({...config.values,[titleField.key]:title,...(number===undefined?{}:{[numberField.key]:number})});
  async function createApi(page,title,number,registry=registryId,expected=201) {
    const result=await api(page,'/documents','POST',{registry_id:registry,document_type_id:typeId,schema_version_id:version.id,
      card_state:'draft',publication_targets:['internal'],values:cardValues(title,number)},expected);
    if(expected===201)created.push(result.id);return result;
  }
  async function newForm(page,title,registry=registryId) {
    await route(page,registry,'&document_type_id='+typeId);
    await page.getByRole('group',{name:'Вид и сортировка документов',exact:true}).getByRole('button',{name:'Создать документ',exact:true}).click();
    const pane=page.locator('.document-create');await pane.waitFor();
    await pane.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(typeId);
    await named(pane,titleField).fill(title);
    for(const [key,value]of Object.entries(config.values||{})) {
      if(key===titleField.key||key===numberField.key)continue;
      const field=flat(version.schema).find(field=>field.key===key);assert(field,'Unknown configured fixture field');
      assert(['text','textarea','integer','decimal','date','datetime'].includes(field.type),'UI fixture defaults must use scalar editable fields');
      await named(pane,field).fill(String(value??''));
    }
    return pane;
  }
  async function saveNew(page,pane,expected=201) {
    const response=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/documents'&&response.request().method()==='POST');
    await pane.getByRole('button',{name:'Создать документ',exact:true}).click();
    const result=await response;assert.equal(result.status(),expected,'Real UI create response');
    const card=await result.json();if(expected===201){created.push(card.id);await page.getByRole('button',{name:'Сохранить карточку',exact:true}).waitFor();}
    return {card,request:result.request().postDataJSON()};
  }
  try {
    editor=await session(users.editor);second=await session(users.second);viewer=await session(users.viewer);
    const identities=await Promise.all([editor,second,viewer].map(page=>api(page,'/users/me')));
    assert.equal(new Set(identities.map(user=>user.id)).size,3,'Independent identities');await api(editor,'/health/ready');
    if(config.expectedSha)assert.equal((await api(editor,'/admin/platform')).source_sha,config.expectedSha);
    version=(await api(editor,`/document-types/${typeId}/versions`)).filter(item=>item.state==='published').sort((a,b)=>b.version-a.version)[0];
    assert(version,'Published fixture schema');const fields=flat(version.schema);
    titleField=fields.find(field=>field.system==='title');numberField=fields.find(field=>field.system==='reg_number');assert(titleField&&numberField);
    const books=await api(editor,'/references');
    const configured=Array.isArray(config.references)?config.references[0]:undefined;
    lookupField=configured?fields.find(field=>field.key===configured.field_key):fields.find(field=>field.type==='reference'&&field.filterable&&!field.visible_when&&(field.reference_code||field.reference_set_id));
    assert(lookupField?.type==='reference','Fixture needs an editable filterable single-reference field');
    const book=books.find(book=>book.id===(configured?.reference_set_id||lookupField.reference_set_id))||books.find(book=>book.code===lookupField.reference_code&&book.registry_id===registryId)||books.find(book=>book.code===lookupField.reference_code&&!book.registry_id);
    assert(book,'Authorized fixture reference book');
    reference={...configured,items:configured?.items||await api(editor,`/references/${book.id}/items?mode=editor`)};
    const active=reference.items.filter(item=>item.active!==false&&item.selectable!==false);assert(active.length>=2,'Lookup regression requires at least two active UUID choices');
    const target=active.find(item=>item.id===reference.expected_id)||active[0];
    const normalize=text=>text.trim().replace(/\s+/gu,' ').toLocaleLowerCase('ru');
    let uniqueQuery=reference.query;
    if(!uniqueQuery)for(let length=3;length<=target.label.length;length++){const candidate=target.label.slice(0,length);if(active.filter(item=>normalize(item.label).includes(normalize(candidate))).length===1){uniqueQuery=candidate;break;}}
    assert(uniqueQuery&&normalize(uniqueQuery).length>=3,'Provide a unique lookup query of at least three characters');
    pass('Strict HTTPS, three independent users, healthy deployed revision and owned schema/reference fixtures');

    stage='stable search timing and complete zero-result catalog';
    await route(editor,registryId,'&q='+marker);await editor.bringToFront();
    const form=editor.locator('.search-form');
    assert.equal(await form.getByRole('combobox',{name:'Тип',exact:true}).inputValue(),'','Zero-result catalog must use all document types');
    await form.getByRole('searchbox',{name:'Поиск',exact:true}).fill(marker);
    assert.equal((await search(editor)).total,0);
    const duration=await editor.locator('.search-status').innerText();let polls=0;
    const onPoll=response=>{if(new URL(response.url()).pathname==='/api/v1/search'&&response.request().method()==='POST'&&response.status()===200)polls++;};
    editor.on('response',onPoll);
    await editor.evaluate(()=>{
      const status=document.querySelector('.search-status');window.__searchNumberTiming={initial:status.textContent,changed:[]};
      window.__searchNumberObserver=new MutationObserver(()=>{const value=status.textContent;if(value!==window.__searchNumberTiming.initial)window.__searchNumberTiming.changed.push(value);});
      window.__searchNumberObserver.observe(status,{childList:true,characterData:true,subtree:true,attributes:true});
    });
    await eventually(()=>polls>=2,'Two real background search polls did not arrive',18000);await delay(300);editor.off('response',onPoll);
    assert.equal(await editor.locator('.search-status').innerText(),duration);
    assert.deepEqual(await editor.evaluate(()=>{window.__searchNumberObserver.disconnect();return window.__searchNumberTiming.changed;}),[],'Background polls must never replace elapsed search time or show searching');
    await form.getByRole('button',{name:'Фильтры',exact:true}).click();
    await form.getByRole('button',{name:'Добавить условие',exact:true}).click();
    const fieldSelector=form.getByRole('combobox',{name:'Поле фильтра 1',exact:true});
    const eligible=fields.filter(field=>field.filterable&&!field.visible_when&&!['file','group'].includes(field.type));
    await eventually(async()=>{const options=await fieldSelector.locator('option').evaluateAll(options=>options.map(option=>option.value));return eligible.every(field=>options.some(key=>key===field.key||key.endsWith(':'+field.key)));},'All eligible fields must remain available for all types with zero results');
    if(config.expectedFilterCount!==undefined)assert.equal(eligible.length,config.expectedFilterCount);
    for(const key of ['should_publish','published_where_id','published_date','number','removed_from_control'])if(fields.some(field=>field.key===key))assert(eligible.some(field=>field.key===key),key);
    await form.getByRole('combobox',{name:'База публикации карточки',exact:true}).selectOption('shared');
    const boolean=eligible.find(field=>field.key==='should_publish')||eligible.find(field=>field.type==='boolean');assert(boolean,'Tri-state filter fixture');
    await fieldSelector.selectOption(boolean.key);
    const bool=form.getByRole('combobox',{name:'Значение фильтра 1',exact:true});
    for(const [option,value]of [['true',true],['false',false],['unspecified',{op:'absent'}]]){
      await bool.selectOption(option);await search(editor);
      assert.deepEqual(JSON.parse(new URL(editor.url()).searchParams.get('filters'))[boolean.key],value);
      assert.equal(new URL(editor.url()).searchParams.get('publication_target'),'shared');
    }
    pass('Search elapsed time survives two real background polls; zero-result/all-types catalog and distinct publication tri-state filters remain usable');
    await screenshot(editor,'01-stable-search-and-catalog');

    stage='single overlay filter lookup';
    await fieldSelector.selectOption(lookupField.key);
    const combo=form.getByRole('combobox',{name:'Значение фильтра 1',exact:true});
    const button=form.getByRole('button',{name:'Найти',exact:true});
    const baseline=await button.boundingBox();await combo.click();
    await editor.getByRole('listbox',{name:'Значение фильтра 1',exact:true}).waitFor();
    const afterOpen=await button.boundingBox();assert(Math.abs(baseline.y-afterOpen.y)<=1,'Opening reference dropdown must not shift search controls');
    assert.equal(await form.locator('.lookup-control input').count(),1,'One editable lookup input');
    assert.equal(await editor.locator('body > .lookup-popup').count(),1,'One body-level overlay');
    await combo.fill(uniqueQuery);
    await eventually(()=>form.locator('.lookup-clear').isVisible(),'Unique active >=3 character query should auto-select UUID');
    await search(editor);assert.equal(JSON.parse(new URL(editor.url()).searchParams.get('filters'))[lookupField.key],target.id);
    await combo.click();await combo.fill(marker+'_unknown');await combo.press('Escape');
    await search(editor);assert.equal(JSON.parse(new URL(editor.url()).searchParams.get('filters'))[lookupField.key],target.id,'Unknown free text must not become a filter value');
    pass('Single body overlay has no opening layout shift, uniquely matching active choices auto-select existing UUIDs, unknown text never commits');

    stage='new-card unsaved reservation';
    let pane=await newForm(editor,marker+' automatic');
    await named(pane,lookupField).click();await named(pane,lookupField).fill(uniqueQuery);
    await eventually(()=>pane.locator('.lookup-clear').isVisible(),'New-card lookup must select the unique active UUID');
    await named(pane,titleField).click();
    const beforeRequests=await api(editor,`/documents?registry_id=${registryId}&document_type_id=${typeId}&mode=editor`);
    const reserving=editor.waitForResponse(response=>new URL(response.url()).pathname===`/api/v1/registries/${registryId}/next-number`&&response.request().method()==='POST');
    await pane.getByRole('button',{name:'Присвоить номер',exact:true}).click();
    const reservedResponse=await reserving;assert.equal(reservedResponse.status(),200);const reservation=await reservedResponse.json();
    assert.equal(await named(pane,titleField).inputValue(),marker+' automatic');
    await eventually(async()=>await named(pane,numberField).inputValue()===reservation.number,'Reservation response must reach the unsaved number input');
    const afterRequests=await api(editor,`/documents?registry_id=${registryId}&document_type_id=${typeId}&mode=editor`);
    assert.equal(afterRequests.total,beforeRequests.total,'Reserve must not create/save a card');
    const automatic=await saveNew(editor,pane);
    assert.equal(automatic.request.number_entry_id,reservation.number_entry_id);
    assert.equal(automatic.request.values[lookupField.key],target.id);
    assert.equal(automatic.card.reg_number,reservation.number);assert.equal(automatic.card.number_locked,false);
    pass('New-card UI reserves without saving, preserves unsaved fields/lookup UUID and attaches exactly that receipt on create');
    await screenshot(editor,'02-automatic-card');

    stage='manual duplicate remains editable';
    const manualNumber=marker+'/manual';await createApi(second,marker+' occupied',manualNumber);
    pane=await newForm(editor,marker+' corrected');
    await pane.getByRole('button',{name:'Ввести произвольный',exact:true}).click();
    await named(pane,numberField).fill('  '+manualNumber.toUpperCase()+'  ');
    const conflict=await saveNew(editor,pane,409);assert.equal(conflict.card.error.code,'number_conflict');
    assert.equal(await named(pane,titleField).inputValue(),marker+' corrected');
    await eventually(()=>named(pane,numberField).isEditable(),'Duplicate number must remain editable');
    const corrected=manualNumber+'/free';await named(pane,numberField).fill(corrected);
    const saved=await saveNew(editor,pane);assert.equal(saved.card.reg_number,corrected);assert.equal(saved.card.number_locked,false);
    assert.equal((await createApi(second,marker+' other registry',manualNumber,otherRegistryId)).reg_number,manualNumber);
    pass('Case/trim duplicate produces409 while retaining editable draft; correction saves; identical number is valid in a different registry');
    await screenshot(editor,'03-manual-corrected');

    stage='two-session concurrent manual collision';
    const collision=marker+'/concurrent';
    const body={registry_id:registryId,document_type_id:typeId,schema_version_id:version.id,values:cardValues(marker+' collision',collision)};
    const concurrent=await Promise.all([editor,second].map(page=>apiResult(page,'/documents','POST',body)));
    assert.deepEqual(concurrent.map(result=>result.status).sort(),[201,409]);
    const winner=concurrent.find(result=>result.status===201);created.push(winner.data.id);
    assert.equal(concurrent.find(result=>result.status===409).data.error.code,'number_conflict');
    pass('Two independent real editor sessions racing the same manual number yield exactly one201 and one409');

    stage='viewer write denial';
    const shared=await api(editor,`/documents/${saved.card.id}`,'PUT',{revision:saved.card.revision,values:{},card_state:'published',publication_targets:['internal','shared']});
    await api(viewer,`/documents/${shared.id}?mode=viewer`);
    await route(viewer,registryId,'&document='+shared.id,true);
    await viewer.getByRole('button',{name:'Содержание',exact:true}).waitFor();
    assert.equal(await viewer.getByRole('button',{name:'Создать документ',exact:true}).count(),0);
    assert.equal(await viewer.getByRole('button',{name:'Сохранить карточку',exact:true}).count(),0);
    await api(viewer,`/registries/${registryId}/next-number`,'POST',{document_type_id:typeId,idempotency_key:randomUUID()},403);
    await createApi(viewer,marker+' denied',marker+'/viewer',registryId,403);
    await api(viewer,`/documents/${shared.id}`,'PUT',{revision:shared.revision,values:{[numberField.key]:marker+'/viewer-write'}},403);
    assert.deepEqual(errors,[]);pass('Viewer has no creation UI and cannot reserve, create or update registration numbers; no page runtime errors');
    stage='complete';return {status:'PASS',checks,browserErrors:errors.length,createdDocumentIds:created};
  }catch(error){
    await fs.writeFile(path.join(outputDir,'failure.txt'),stage+'\n'+String(error.stack||error));
    for(const[index,page]of pages.entries())await screenshot(page,'failure-'+index).catch(()=>{});
    throw error;
  }finally{
    await fs.writeFile(path.join(outputDir,'checks.json'),JSON.stringify({stage,checks,browserErrors:errors.length,createdDocumentIds:created},null,2));
    for(const context of contexts)await context.close();
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){
  let browser;
  try{
    assert(process.argv[2],'Supply a private disposable-environment JSON config path');
    const config=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
    const{chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');
    browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});
    const receipt=await runSearchNumberWorkflow({...config,browser});
    await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));
    console.log(`Search/number HTTPS PASS: ${receipt.checks.length} scenarios.`);
  }catch(error){console.error(`Search/number FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}
  finally{await browser?.close();}
}
