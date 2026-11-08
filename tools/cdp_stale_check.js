// 验收 A05：结果与配置隔离（参数改动标过期、切数据集清空结果、无迟到响应覆盖）
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const PORT = 9450 + Math.floor(Math.random() * 8);
const prof = path.join(os.tmpdir(), 'edge_stale_' + Date.now());

const child = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + prof,
  '--window-size=1600,1000', URL
], { stdio: 'ignore' });

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  let target = null;
  for (let i = 0; i < 80; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      target = list.find(x => x.type === 'page' && x.url.startsWith('http'));
      if (target) break;
    } catch (e) { }
    await sleep(250);
  }
  if (!target) throw new Error('no target');

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => { ws.onopen = r; });

  let msgId = 0;
  const pending = new Map();
  const errors = [];
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
  };
  function send(method, params = {}) {
    const id = ++msgId;
    return new Promise((res, rej) => { pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params })); });
  }
  async function evalJs(expression) {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || 'eval error');
    return r.result?.value;
  }
  // React 受控 input/select 必须走原生 setter + 事件
  const setInput = (sel, val) => evalJs(`(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return 'not-found';
    const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(String(val))});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value;
  })()`);

  const snap = () => evalJs(`(() => {
    const hint = document.querySelector('.stale-hint');
    const empty = document.querySelector('.stat-card.empty');
    const big = document.querySelector('.stat-card .big');
    return JSON.stringify({
      stale: !!hint,
      hintText: hint ? hint.textContent.slice(0, 24) : null,
      hasResult: !empty,
      total: big ? big.textContent : null,
    });
  })()`);

  await send('Runtime.enable');
  await sleep(5500);

  console.log('1) 初始:', await snap());
  await evalJs(`(() => { const b=[...document.querySelectorAll('.topbar button')].find(x=>x.textContent.includes('运行回测')); b.click(); return 'clicked' })()`);
  await sleep(4000);
  console.log('2) 回测后:', await snap());

  console.log('3) 改本金 → 应标过期:', await setInput('.global-card input[type=number]', '50000'));
  await sleep(900);
  console.log('4) 改后状态:', await snap());

  console.log('5) 恢复本金:', await setInput('.global-card input[type=number]', '100000'));
  await sleep(900);
  console.log('6) 恢复后:', await snap());

  // 切到另一个数据集：旧结果必须清空
  const dsCount = await evalJs(`document.querySelectorAll('.topbar select')[0].options.length`);
  if (dsCount > 1) {
    console.log('7) 切换数据集:', await evalJs(`(() => {
      const s = document.querySelectorAll('.topbar select')[0];
      const target = s.value === s.options[1].value ? s.options[0] : s.options[1];
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(s, target.value);
      s.dispatchEvent(new Event('change', { bubbles: true }));
      return target.textContent;
    })()`));
    await sleep(2500);
    console.log('8) 切换后:', await snap());
  } else {
    console.log('7) 只有一个数据集，跳过切换测试');
  }

  console.log('9) JS 异常:', errors.length ? errors : '无');
  ws.close(); child.kill(); process.exit(0);
})().catch(e => { console.error('ERR', e.message); child.kill(); process.exit(1); });
