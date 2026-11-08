// 验收：左右侧栏折叠（按钮 + 快捷键 L/R + 图表自适应 + 偏好持久化）
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const PORT = 9440 + Math.floor(Math.random() * 8);
const prof = path.join(os.tmpdir(), 'edge_sidefold_' + Date.now());

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
  async function realClick(sel) {
    const box = await evalJs(`(() => {
      const el = document.querySelector(${JSON.stringify(sel)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    })()`);
    if (!box) return 'not-found';
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    return 'clicked';
  }
  const snap = () => evalJs(`(() => {
    const L = document.querySelector('.left'), R = document.querySelector('.right');
    const lb = L.querySelector('.side-body'), rb = R.querySelector('.side-body');
    const lt = L.querySelector('.side-title'), rt = R.querySelector('.side-title');
    const c = window.__chart;
    return JSON.stringify({
      leftW: Math.round(L.getBoundingClientRect().width),
      rightW: Math.round(R.getBoundingClientRect().width),
      leftCollapsed: L.classList.contains('collapsed'),
      rightCollapsed: R.classList.contains('collapsed'),
      leftBodyShown: !!lb.offsetParent,
      rightBodyShown: !!rb.offsetParent,
      leftTitleVert: getComputedStyle(lt).writingMode,
      rightTitleVert: getComputedStyle(rt).writingMode,
      leftIcon: L.querySelector('.side-fold').textContent,
      rightIcon: R.querySelector('.side-fold').textContent,
      chartW: c ? c.getWidth() : null,
    });
  })()`);

  await send('Runtime.enable');
  await sleep(5000);
  await send('Page.enable');

  console.log('1) 初始:', await snap());

  console.log('2) 点击收起左栏:', await realClick('.left .side-fold'));
  await sleep(700);
  console.log('3) 左栏收起后:', await snap());

  console.log('4) 快捷键 L 展开:', await evalJs(`(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'l', bubbles: true }));
    await new Promise(r => setTimeout(r, 700)); return 'ok';
  })()`), await snap());

  console.log('5) 点击收起右栏:', await realClick('.right .side-fold'));
  await sleep(700);
  console.log('6) 右栏收起后:', await snap());

  console.log('7) 快捷键 R 展开:', await evalJs(`(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'r', bubbles: true }));
    await new Promise(r => setTimeout(r, 700)); return 'ok';
  })()`), await snap());

  console.log('8) 两栏同时收起:', await evalJs(`(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'l', bubbles: true }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'r', bubbles: true }));
    await new Promise(r => setTimeout(r, 900)); return 'ok';
  })()`), await snap());

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  const fs = require('fs');
  const out = path.resolve(__dirname, '..', 'frontend', 'ui_side_collapsed.png');
  fs.writeFileSync(out, Buffer.from(shot.data, 'base64'));
  console.log('9) 截图:', out, fs.statSync(out).size);

  console.log('10) localStorage:', await evalJs(`localStorage.getItem('wb.chart.pref')`));

  // 输入框内按 L 不应触发折叠
  console.log('11) 输入框内按 L 不折叠:', await evalJs(`(async () => {
    const inp = document.querySelector('.param input[type=number]');
    if (!inp) return 'no-input';
    inp.focus();
    inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'l', bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    return JSON.stringify({ leftCollapsed: document.querySelector('.left').classList.contains('collapsed') });
  })()`));

  console.log('12) JS 异常:', errors.length ? errors : '无');
  ws.close(); child.kill(); process.exit(0);
})().catch(e => { console.error('ERR', e.message); child.kill(); process.exit(1); });
