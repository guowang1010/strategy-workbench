// 悬停路径验证：通过 zrender handler 派发 mousemove（等价真实鼠标移动）
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const OUTDIR = path.resolve(__dirname, '..', 'frontend');
const PORT = 9398;
const prof = path.join(os.tmpdir(), 'edge_hover_' + Date.now());
const child = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + prof,
  '--window-size=1600,1100', '--force-device-scale-factor=2', URL
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
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => { ws.onopen = r; });
  let msgId = 0; const pending = new Map();
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); }
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++msgId; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async e => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || 'eval error');
    return r.result?.value;
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await sleep(5000);

  // 获取图表容器在视口中的偏移，把「图表内某点」换算成视口坐标，再派发给 zrender
  const offset = await evalJs(`(() => { const c = document.querySelector('.chart'); const r = c.getBoundingClientRect(); return JSON.stringify({x: r.left, y: r.top, w: r.width, h: r.height}); })()`);
  const { x: ox, y: oy, w: cw } = JSON.parse(offset);
  const px = Math.round(ox + cw * 0.6), py = Math.round(oy + 150);

  await evalJs(`(() => {
    const zr = (window).__chart.getZr();
    zr.handler.dispatch('mousemove', {
      type: 'mousemove', event: {}, target: null, topTarget: null,
      offsetX: ${px - Math.round(ox)}, offsetY: 150,
      clientX: ${px}, clientY: ${py}, zrX: ${px - Math.round(ox)}, zrY: 150,
    });
    return 1;
  })()`);
  await sleep(1200);

  const hoverInfo = await evalJs(`(() => {
    const c = document.querySelector('.chart');
    const d = c && [...c.querySelectorAll('div')].find(x =>
      getComputedStyle(x).position === 'absolute' && (x.innerText || '').match(/\\d{4}-\\d{2}-\\d{2}/) && x.offsetWidth > 100);
    if (!d) return JSON.stringify({ visible: false });
    const cs = getComputedStyle(d);
    return JSON.stringify({
      visible: true, w: d.offsetWidth, bg: cs.backgroundColor, radius: cs.borderRadius,
      text: d.innerText.replace(/\\n/g, ' | ').slice(0, 180),
    });
  })()`);
  console.log('[zrender hover]', hoverInfo);

  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(path.join(OUTDIR, 'ui_tooltip_hover.png'), Buffer.from(shot.data, 'base64'));
  console.log('shot:', fs.statSync(path.join(OUTDIR, 'ui_tooltip_hover.png')).size);

  ws.close(); child.kill(); process.exit(0);
})().catch(e => { console.error('ERR', e.message); child.kill(); process.exit(1); });
