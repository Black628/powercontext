// 采集测量环境：Node / OS / CPU / 内存 / seekdb 版本与端口。
// 用法：node scripts/env-info.mjs
import os from 'node:os';
import { config } from '../server/config.mjs';
import { getClient, stats } from '../server/seekdb.mjs';

const cpus = os.cpus();
const info = {
  measuredAt: new Date().toISOString(),
  node: process.version,
  execPath: process.execPath,
  os: `${os.platform()} ${os.release()} (${os.arch()})`,
  osName: os.version?.(),
  cpu: cpus[0]?.model,
  cores: cpus.length,
  totalMemGB: Number((os.totalmem() / 1024 ** 3).toFixed(1)),
  freeMemGB: Number((os.freemem() / 1024 ** 3).toFixed(1)),
  httpPort: config.httpPort,
  seekdb: { ...config.seekdb, probePorts: undefined },
};

const client = await getClient();
const v = await client.execute('select version() as v, @@version_comment as vc');
info.seekdb.version = v?.[0]?.v;
info.seekdb.versionComment = v?.[0]?.vc;
info.seekdb.connectedPort = config.seekdb.port;
const cols = await client.listCollections().catch(() => []);
info.seekdb.collections = cols.map?.((c) => c?.name ?? c) ?? cols;
try {
  info.collectionStats = await stats();
} catch (e) {
  info.collectionStats = { error: e.message };
}

console.log(JSON.stringify(info, null, 2));
process.exit(0);
