import {db} from './context.mjs';
const original=process.env.DATABASE_URL;if(!original)throw Error('DATABASE_URL required');
const u=new URL(original);if(u.pathname!=='/sdd')throw Error('Demo requires dedicated sdd database');u.pathname='/';process.env.DATABASE_URL=u.toString();
const c=await db();try{await c.execute('CREATE DATABASE IF NOT EXISTS sdd');}finally{await c.end();}
