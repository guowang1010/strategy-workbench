// CDP 控制台诊断：抓取页面 console / 异常 / 网络失败，定位前端初始化问题
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE = process.argv[2] || 'http://127.0.0.1:8765/';
const PORT = 9345;
const prof = path.join(os.tmpdir(), 'edge_cons_' + Date.now());

const child = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + prof,
  '--window-size=1600,1000', PAGE
], { stdio: 'ignore' });

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const t = list.find(x => x.type === 'page' && x.url.startsWith('http'));
      if (t && t.webSocketDebuggerUrl) return t;
    } catch (e) { }
    await sleep(300);
  }
  throw new Error('no target');
}

function connect(url) {
  return new Promise((res, rej) => {
    const ws = new WebSocket(url);
    ws.onopen = () => res(ws);
    ws.onerror = () => rej(new Error('ws error'));
  });
}

let msgId = 0;
const pending = new Map();
const logs = [];
function send(ws, method, params = {}) {
  const id = ++msgId;
  return new Promise((res, rej) => {
    pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

(async () => {
  const target = await getTarget();
  const ws = await connect(target.webSocketDebuggerUrl);
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
      return;
    }
    if (m.method === 'Runtime.consoleAPICalled') {
      logs.push('[console.' + m.params.type + '] ' +
        m.params.args.map(a => a.value !== undefined ? a.value : (a.description || a.type)).join(' '));
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      logs.push('[EXCEPTION] ' + (d.exception?.description || d.text));
    }
    if (m.method === 'Network.loadingFailed') {
      logs.push('[net-fail] ' + m.params.errorText + ' ' + (m.params.type || ''));
    }
    if (m.method === 'Network.responseReceived') {
      const r = m.params.response;
      if (r.url.includes('/api/')) logs.push('[api] ' + r.status + ' ' + r.url);
    }
  };

  await send(ws, 'Runtime.enable');
  await send(ws, 'Network.enable');
  await send(ws, 'Page.enable');
  await send(ws, 'Page.reload', { ignoreCache: true });
  await sleep(6000);

  const probe = await send(ws, 'Runtime.evaluate', {
    expression: `JSON.stringify({
      models: (window.__zustand_probe || null),
      bodyLen: document.body.innerText.length,
      head: document.body.innerText.slice(0, 200),
      opts: document.querySelectorAll('select option').length,
      cards: document.querySelectorAll('.model-card').length,
      sel1: document.querySelectorAll('select')[0]?.value,
    })`, returnByValue: true,
  });
  console.log('--- PROBE ---');
  console.log(probe.result?.value);
  console.log('--- LOGS ---');
  logs.forEach(l => console.log(l));
  ws.close();
  child.kill();
  process.exit(0);
})().catch(e => { console.error('ERR', e); child.kill(); process.exit(1); });
