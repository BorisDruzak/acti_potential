/** Real HTTPS reference acceptance: fresh synthetic fixtures only.
 * Run: node scripts/e2e_task003_references.mjs <ignored-config.json>
 * Private config keys: origin, adminCredentialsFile, playwrightModule, outputDir.
 * Screenshots and receipts are written only to the caller's ignored output path.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

export async function referenceSupplement({browser,origin,admin,outputDir}) {
  assert.equal(new URL(origin).protocol,'https:');
  const context=await browser.newContext({viewport:{width:1440,height:1000},ignoreHTTPSErrors:false});
  const page=await context.newPage(); page.setDefaultTimeout(20000);
  const suffix=Date.now().toString(36);
  const title='Synthetic hierarchy '+suffix;
  const results=[];
  let stage='login';
  async function mutation(endpoint,action,method='POST',expected=201) {
    const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v1'+endpoint&&r.request().method()===method);
    await action(); const response=await pending;
    assert.equal(response.status(),expected,`${method} ${endpoint} UI status`);
    return response.json();
  }
  async function json(endpoint,method='GET',data,expected=200) {
    const result=await page.evaluate(async ({endpoint,method,data})=>{
      const me=await(await fetch('/api/v1/users/me')).json();
      const response=await fetch('/api/v1'+endpoint,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':me.csrf_token},body:data===undefined?undefined:JSON.stringify(data)});
      return {status:response.status,body:await response.json().catch(()=>null)};
    },{endpoint,method,data});
    assert.equal(result.status,expected,`${method} ${endpoint} status`);
    return result.body;
  }
  try {
    await page.goto(origin);
    await page.getByRole('textbox',{name:'Логин',exact:true}).fill(admin.login);
    await page.getByLabel('Пароль',{exact:true}).fill(admin.password);
    await page.getByRole('button',{name:'Войти в рабочее пространство'}).click();
    await page.getByRole('button',{name:'Выйти',exact:true}).waitFor();
    const platform=await json('/admin/platform');
    await page.getByRole('navigation').getByRole('button',{name:'Справочники',exact:true}).click();
    await page.getByRole('button',{name:'Создать справочник',exact:true}).click();
    await page.getByLabel('Название справочника',{exact:true}).fill(title);
    await page.getByLabel('Код справочника',{exact:true}).fill('e2e_hierarchy_'+suffix);
    const ref=await mutation('/references',()=>page.locator('form').getByRole('button',{name:'Создать справочник',exact:true}).click());
    stage='parent creation';
    const endpoint='/references/'+ref.id;
    let panel=page.locator('section.panel').filter({has:page.getByRole('heading',{name:title,exact:true})});
    await panel.locator('summary').filter({hasText:/^Добавить значение$/}).click();
    const add=panel.locator('form').filter({has:page.getByLabel('Новое значение',{exact:true})});
    async function create(label,code,parent,from,until) {
      await add.getByLabel('Новое значение',{exact:true}).fill(label);
      await add.getByLabel('Код значения',{exact:true}).fill(code);
      await add.getByLabel('Родительское значение',{exact:false}).selectOption(parent||'');
      await add.getByLabel('Действует с',{exact:true}).fill(from||'');
      await add.getByLabel('Действует по',{exact:true}).fill(until||'');
      return mutation(endpoint+'/items',()=>add.getByRole('button',{name:'Добавить значение',exact:true}).click());
    }
    const parent=await create('Synthetic Parent '+suffix,'p',null,'2000-01-01','2099-12-31');
    await panel.getByRole('button',{name:'Изменить: '+parent.label,exact:true}).waitFor();
    stage='child creation';
    const child=await create('Synthetic Child '+suffix,'c',parent.id,'2000-01-01','2099-12-31');
    assert.equal(child.parent_id,parent.id); assert.equal(child.selectable,true);
    results.push('hierarchy and validity configured through UI');
    await panel.locator('summary').filter({hasText:/^Добавить значение$/}).click();
    await panel.getByRole('button',{name:'Изменить: '+parent.label,exact:true}).click();
    stage='cycle validation';
    let edit=panel.locator('form').filter({has:page.getByLabel('Значение: '+parent.label,{exact:true})});
    await edit.getByLabel('Родительское значение',{exact:false}).selectOption(child.id);
    await mutation(endpoint+'/items/'+parent.id,()=>edit.getByRole('button',{name:'Сохранить значение',exact:true}).click(),'PATCH',422);
    await edit.getByRole('alert').waitFor();
    await edit.getByRole('button',{name:'Отмена',exact:true}).click();
    assert.equal((await json(endpoint+'/items')).find(i=>i.id===parent.id).parent_id,null);
    results.push('cycle blocked in UI and PostgreSQL unchanged');
    await panel.getByRole('button',{name:'Изменить: '+child.label,exact:true}).click();
    stage='expiry validation';
    edit=panel.locator('form').filter({has:page.getByLabel('Значение: '+child.label,{exact:true})});
    await edit.getByLabel('Действует по',{exact:true}).fill('2001-01-01');
    const expired=await mutation(endpoint+'/items/'+child.id,()=>edit.getByRole('button',{name:'Сохранить значение',exact:true}).click(),'PATCH',200);
    assert.equal(expired.selectable,false);
    await panel.getByText(/вне срока действия/).waitFor();
    results.push('expired item becomes unselectable through UI');
    await json(endpoint+'/items/'+child.id,'PATCH',{valid_from:'2030-01-01',valid_until:'2020-01-01'},422);
    await json(endpoint+'/items/'+child.id,'PATCH',{source_database:'forbidden',source_entity:'forbidden',source_id:'00001'},422);
    const payload=Buffer.from('source_database,source_entity,source_id,label\nforbidden,forbidden,00001,Forbidden\n').toString('base64');
    await json(endpoint+'/import','POST',{format:'csv',content:payload},422);
    assert.equal((await json(endpoint+'/items')).length,2);
    results.push('invalid dates and source-qualified identity writes denied');
    await page.reload();
    stage='reload persistence';
    await page.getByRole('navigation').getByRole('button',{name:'Справочники',exact:true}).click();
    await page.locator('label.picker select').selectOption(ref.id);
    panel=page.locator('section.panel').filter({has:page.getByRole('heading',{name:title,exact:true})});
    await panel.getByText(/вне срока действия/).waitFor();
    assert((await panel.innerText()).includes(parent.label));
    assert((await panel.innerText()).includes(child.label));
    results.push('hierarchy and dates survive reload');
    await fs.mkdir(outputDir,{recursive:true});
    await page.screenshot({path:path.join(outputDir,'reference-hierarchy.png'),fullPage:true});
    const receipt={status:'PASS',source_sha:platform.source_sha,checks:results,reference_id:ref.id};
    await fs.writeFile(path.join(outputDir,'reference-supplement.json'),JSON.stringify(receipt,null,2));
    console.log('Reference supplement PASS: '+results.length+' scenarios.');
    return receipt;
  } catch(error) {
    await fs.mkdir(outputDir,{recursive:true});
    await page.screenshot({path:path.join(outputDir,'reference-failure.png'),fullPage:true}).catch(()=>{});
    await fs.writeFile(path.join(outputDir,'reference-failure.txt'),stage+'\n'+error.stack);
    throw new Error(stage+': '+error.name+': '+error.message.split('\n')[0]);
  } finally {await context.close();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  let browser;
  try {
    const config=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
    const admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));
    const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');
    browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});
    await referenceSupplement({browser,origin:config.origin,admin,outputDir:path.join(config.outputDir,'reference-supplement')});
  } catch(error) {console.error('Reference supplement FAIL: '+error.name+': '+error.message.split('\n')[0]);process.exitCode=1;}
  finally {await browser?.close();}
}
