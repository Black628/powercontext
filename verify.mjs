import {spawnSync} from 'node:child_process';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {root,sha} from './context.mjs';
const file=path.join(root,'business/orders.mjs');const after=await readFile(file,'utf8'),before=await readFile(path.join(root,'snapshots/before.mjs'),'utf8');
const records=[];
function run(label,tests){const args=['--test',...tests.map(x=>'tests/'+x+'.test.mjs')];const r=spawnSync(process.execPath,args,{cwd:root,encoding:'utf8'});records.push({label,command:[process.execPath,...args],exit_code:r.status,stdout:r.stdout,stderr:r.stderr,source_sha256:sha(label.startsWith('before')?before:after)});return r.status;}
let ok=false;
try{await writeFile(file,before);const bf=run('before-feature',['feature']);const br=run('before-regression',['regression']);await writeFile(file,after);const af=run('after-all',['feature','regression']);ok=bf===1&&br===0&&af===0;}finally{await writeFile(file,after);await mkdir(path.join(root,'evidence'),{recursive:true});const version=spawnSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).stdout.trim();await writeFile(path.join(root,'evidence/tests.json'),JSON.stringify({at:new Date().toISOString(),git_head:version,tests_sha256:sha((await readFile(path.join(root,'tests/feature.test.mjs'),'utf8'))+(await readFile(path.join(root,'tests/regression.test.mjs'),'utf8'))),ok,records},null,2));}
console.log(JSON.stringify({ok,results:records.map(({label,exit_code})=>({label,exit_code}))},null,2));if(!ok)process.exitCode=1;
