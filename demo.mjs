import {spawnSync} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {root} from './context.mjs';
function run(script,...args){const r=spawnSync(process.execPath,[script,...args],{cwd:root,env:process.env,encoding:'utf8'});if(r.status!==0)throw Error(r.stderr||r.stdout);return r.stdout;}
const cli=(...args)=>run('cli.mjs',...args);
const initial=JSON.parse(cli('context'));if(initial.state.length)throw Error('Use a fresh PowerContext scope for empty-state acceptance');
cli('save',JSON.stringify({done:['sources imported'],todo:['implement status filter','run tests'],checks:[]}));
const restored=JSON.parse(cli('context'));if(!restored.latest?.todo.includes('implement status filter'))throw Error('Restarted process did not reuse task state');
await writeFile(path.join(root,'business/orders.mjs'),await readFile(path.join(root,'snapshots/before.mjs')));cli('ingest');
run('agent.mjs');run('verify.mjs');
cli('save',JSON.stringify({done:['MiniMax modified code','feature and regression tests passed'],todo:[],checks:JSON.parse(await readFile(path.join(root,'evidence/tests.json'),'utf8'))}));
cli('ingest');const final=JSON.parse(cli('context'));if(final.latest.todo.length)throw Error('Final state was not reused');
const spec=path.join(root,'specs/orders.md'),original=await readFile(spec,'utf8');
try{await writeFile(spec,original+'\nUPDATE-1: Status filtering remains case-sensitive in version 2.\n');cli('ingest');const updated=JSON.parse(cli('context'));if(!updated.sources.some(s=>s.body.includes('UPDATE-1')))throw Error('Spec update not retrieved');}finally{await writeFile(spec,original);cli('ingest');}
console.log('Live acceptance completed. Inspect evidence/ for real records.');
