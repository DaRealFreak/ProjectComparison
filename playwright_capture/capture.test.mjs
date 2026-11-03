import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const fixtureUrl = pathToFileURL(join(here, '..', 'tests', 'fixtures', 'states', 'page.html')).href;
const consentUrl = pathToFileURL(join(here, '..', 'tests', 'fixtures', 'states', 'consent.html')).href;

if (!existsSync(join(here, 'node_modules', 'playwright'))) {
  throw new Error('run "npm ci" in playwright_capture first');
}

function writeRequest(overrides) {
  const outDir = mkdtempSync(join(tmpdir(), 'capture-'));
  const request = {
    url: fixtureUrl,
    label: 'live',
    outDir,
    sizes: ['800x600'],
    states: [],
    cookies: [],
    httpCredentials: null,
    userAgent: null,
    omitScreenshots: false,
    timeoutMs: 5000,
    ...overrides,
  };
  const requestFile = join(outDir, 'request.json');
  writeFileSync(requestFile, JSON.stringify(request));
  return { outDir, requestFile };
}

function runCapture(overrides) {
  const { outDir, requestFile } = writeRequest(overrides);
  const run = spawnSync('node', [join(here, 'capture.mjs'), '--request', requestFile], {
    encoding: 'utf8',
  });
  assert.equal(run.status, 0, run.stderr);
  return { outDir, result: JSON.parse(run.stdout) };
}

// Async, so HTTP servers in this process can answer while the helper runs.
function runCaptureAsync(overrides) {
  const { outDir, requestFile } = writeRequest(overrides);
  return new Promise((resolve, reject) => {
    const child = spawn('node', [join(here, 'capture.mjs'), '--request', requestFile]);
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`capture exited with ${code}: ${stderr}${stdout}`));
        return;
      }
      resolve({ outDir, result: JSON.parse(stdout) });
    });
  });
}

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', resolve);
  });
}

function stop(server) {
  server.closeAllConnections();
  return new Promise((resolve) => {
    server.close(resolve);
  });
}

function pngWidth(file) {
  // The PNG header stores the width as a big-endian integer at byte 16.
  return readFileSync(file).readUInt32BE(16);
}

const openBox = (id, extra = {}) => ({
  id,
  when: null,
  steps: [{ action: 'click', selector: '#open', index: 0 }],
  sizes: null,
  ...extra,
});

test('base capture writes html and one screenshot per size', () => {
  const { outDir, result } = runCapture({ sizes: ['800x600'] });
  assert.equal(result.ok, true);
  assert.deepEqual([...result.files].sort(), ['800x600_live.png', 'html_live.html']);
  for (const file of result.files) {
    assert.ok(existsSync(join(outDir, file)), file);
  }
});

test('fullscreen uses a 1920 px viewport whatever size comes before it', () => {
  const { outDir, result } = runCapture({ sizes: ['400x600', 'fullscreen'] });
  assert.equal(result.ok, true);
  assert.equal(pngWidth(join(outDir, '400x600_live.png')), 400);
  assert.equal(pngWidth(join(outDir, 'fullscreen_live.png')), 1920);
});

test('state captured writes files per width', () => {
  const { outDir, result } = runCapture({
    sizes: ['400x600', '1200x800'],
    states: [openBox('box-open')],
  });
  assert.equal(result.states.length, 2);
  for (const entry of result.states) {
    assert.equal(entry.outcome, 'captured');
  }
  for (const size of ['400x600', '1200x800']) {
    assert.ok(existsSync(join(outDir, `state-box-open_${size}_live.png`)));
    const html = readFileSync(join(outDir, `state-box-open_${size}_html_live.html`), 'utf8');
    assert.match(html, /class="show"/);
  }
});

test('outcome per width', () => {
  const { outDir, result } = runCapture({
    sizes: ['400x600', '1200x800'],
    timeoutMs: 500,
    states: [
      {
        id: 'wide',
        when: null,
        steps: [{ action: 'click', selector: '#wide-only', index: 0 }],
        sizes: null,
      },
    ],
  });
  const bySize = Object.fromEntries(result.states.map((entry) => [entry.size, entry]));
  assert.equal(bySize['400x600'].outcome, 'failed');
  assert.match(bySize['400x600'].reason, /step 1/);
  assert.equal(bySize['1200x800'].outcome, 'captured');
  assert.ok(existsSync(join(outDir, 'state-wide_1200x800_live.png')));
  assert.ok(existsSync(join(outDir, 'state-wide_1200x800_html_live.html')));
});

test('trigger absent is absent', () => {
  const { result } = runCapture({
    sizes: ['400x600', '1200x800'],
    states: [openBox('gone', { when: '#missing' })],
  });
  assert.equal(result.states.length, 2);
  for (const entry of result.states) {
    assert.equal(entry.outcome, 'absent');
  }
});

test('storage set by a step does not reach the next load', () => {
  const { result } = runCapture({
    url: consentUrl,
    sizes: ['400x600', '1200x800'],
    states: [
      {
        id: 'accept',
        when: '#banner',
        steps: [{ action: 'click', selector: '#accept', index: 0 }],
        sizes: null,
      },
    ],
  });
  assert.equal(result.states.length, 2);
  for (const entry of result.states) {
    assert.equal(entry.outcome, 'captured', `${entry.size}: ${entry.outcome}`);
  }
});

test('credentials are sent only to the origin of the page', async () => {
  const otherHostAuth = [];
  const otherHost = createServer((request, response) => {
    otherHostAuth.push(request.headers.authorization || null);
    response.writeHead(401, { 'WWW-Authenticate': 'Basic realm="other"' });
    response.end();
  });
  await listen(otherHost);
  const expected = `Basic ${Buffer.from('user:secret').toString('base64')}`;
  const site = createServer((request, response) => {
    if (request.headers.authorization !== expected) {
      response.writeHead(401, { 'WWW-Authenticate': 'Basic realm="site"' });
      response.end();
      return;
    }
    const image = `http://127.0.0.1:${otherHost.address().port}/pixel.png`;
    response.writeHead(200, { 'Content-Type': 'text/html' });
    response.end(`<!doctype html><title>Auth</title><img src="${image}">`);
  });
  await listen(site);
  try {
    const { result } = await runCaptureAsync({
      url: `http://127.0.0.1:${site.address().port}/`,
      httpCredentials: { username: 'user', password: 'secret' },
    });
    assert.equal(result.ok, true, result.error);
    assert.equal(result.status, 200);
    assert.ok(otherHostAuth.length > 0, 'the page did not request the other host');
    assert.deepEqual(otherHostAuth.filter(Boolean), []);
  } finally {
    await stop(site);
    await stop(otherHost);
  }
});
