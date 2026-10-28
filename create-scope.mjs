import {pc} from './context.mjs';
import {randomUUID} from 'node:crypto';
console.log(JSON.stringify(await pc('/v1/scopes',{title:'MiniMax SDD acceptance',summary:'Isolated orders-status task verification',idempotency_key:randomUUID()}),null,2));
