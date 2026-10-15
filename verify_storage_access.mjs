/** Independent NAS access discovery remains usable with offline machine tools. */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { createServer } from 'node:net';
import { Environment } from './environment.mjs';
import { startEnvironmentAccess, credentialHash } from './environment-access.mjs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const base = await mkdtemp(join(tmpdir(), 'storage-access-'));
const listener = createServer();
await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const url = `http://127.0.0.1:${port}/`, key = randomBytes(32).toString('hex'), storageKey = randomBytes(32).toString('hex');
const config = join(base, 'targets.json'), accessConfig = join(base, 'access.json');
let environment, access, client;
try {
  await mkdir(join(base, 'state')); await mkdir(join(base, 'cloud'));
  const storage = { nas: { machine: 'nas', workspace: '/srv/nas/data', webdav: { url: 'https://files.example.org/nas/', account: 'nas-agent', key: storageKey }, smb: { url: 'smb://192.168.1.2/NAS', account: 'pi', key: 'must-not-be-catalogued' } } };
  await writeFile(config, JSON.stringify({ targets: { nas: { host: 'offline.invalid', workspace: '/srv/nas/data' } }, storage }));
  await writeFile(accessConfig, JSON.stringify({ version: 1, url, port, account: 'owner', credentialHash: credentialHash('owner', key) }), { mode: 0o600 });
  environment = new Environment({ stateDir: join(base, 'state'), config, cloudWorkspace: join(base, 'cloud') });
  access = await startEnvironmentAccess({ configFile: accessConfig, environment, reloadTargets() {}, callTool() { throw new Error('Storage access must not dispatch a machine tool'); } });
  async function request(input, authorized = true) {
    return fetch(url + 'api/environment', { method: 'POST', headers: { 'Content-Type': 'application/json', ...(authorized ? { Authorization: 'Basic ' + Buffer.from('owner:' + key).toString('base64') } : {}) }, body: JSON.stringify(input) });
  }
  const protocol = await (await fetch(url)).text();
  assert(!protocol.includes(storageKey));
  assert.equal((await request({ action: 'storage', target: 'nas' }, false)).status, 401);
  const catalog = await (await request({ action: 'list' })).json();
  assert.equal(catalog.storage[0].webdav.url, storage.nas.webdav.url);
  assert(!JSON.stringify(catalog).includes(storageKey));
  assert(!JSON.stringify(catalog).includes('must-not-be-catalogued'));
  const response = await request({ action: 'storage', target: 'nas' });
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.storage.webdav.key, storageKey);
  assert(data.storage.instructions.some(text => text.includes('uncertain MCP write')));
  assert.equal((await request({ action: 'storage' })).status, 400);
  assert.equal((await request({ action: 'storage', target: '__proto__' })).status, 404);
  assert.equal((await request({ action: 'storage', target: 'missing' })).status, 404);
  assert.equal(environment.connections.size, 0);
  console.log('PASS authenticated HTTP discovers independent storage while machine tools remain disconnected; catalogs do not disclose credentials');
  client = new Client({ name: 'storage-verification', version: '1' });
  await client.connect(new StreamableHTTPClientTransport(new URL('mcp', url), { requestInit: { headers: { Authorization: 'Basic ' + Buffer.from('owner:' + key).toString('base64') } } }));
  const result = await client.callTool({ name: 'environment', arguments: { action: 'storage', target: 'nas' } });
  assert(!result.isError && result.content.some(item => item.text?.includes(storageKey)));
  assert.equal(environment.connections.size, 0);
  console.log('PASS standard MCP clients obtain the same WebDAV fallback without starting SSH or executing a model request');
} finally {
  await client?.close(); await access?.close(); await environment?.close(); await rm(base, { recursive: true, force: true });
}
