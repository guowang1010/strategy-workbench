// CDP 驱动：验证页面交互（改参数重算 / 切 tab / 弹层 / 主题切换 / 截图）
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE = 'file:///' + path.resolve(__dirname, '..', 'frontend', 'index.html').replace(/\\/g, '/');
const PORT = 9333;
const SHOT = path.resolve(__dirname, '..', 'frontend', 'shot_dark4.png');
const prof = path.join(os.tmpdir(), 'edge_cdp_' + Date.now());

const child = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + prof,
  '--window-size=1600,1000', '--force-device-scale-factor=2', PAGE
], { stdio: 'ignore' });

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const t = list.find(x => x.type === 'page' && x.url.includes('index.html'));
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
    ws.onerror = e => rej(new Error('ws error'));
  });
}

let msgId = 0;
const pending = new Map();
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
    }
  };
  await send(ws, 'Runtime.enable');
  await send(ws, 'Page.enable');

  const expr = `(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    for (let i = 0; i < 80; i++) {
      if (window.DEMO_DATA && window.state && state.results && state.results.M4) break;
      await sleep(200);
    }
    const out = {};
    out.ready = !!(window.state && state.results && state.results.M4);
    out.selftest = window.__selftest || null;

    const snap = id => {
      const m = state.results[id].metrics;
      return { total: +m.total.toFixed(4), n: m.n, win: +m.win.toFixed(3), exp: +m.exp.toFixed(4),
               mdd: +m.mdd.toFixed(3), exposure: +m.exposure.toFixed(2) };
    };
    out.before = { active: state.active, ids: Object.keys(state.results), M4: snap('M4'), rows: document.querySelectorAll('#tradeBody tr').length };
    out.yAxisBefore = { min: chart.getOption().yAxis[2].min, max: chart.getOption().yAxis[2].max };

    const set = (id, v) => { const el = document.getElementById(id); el.value = v; el.dispatchEvent(new Event('input')); };
    set('p_tp', 14); set('p_sl', 6); set('p_hold', 4); set('p_max', 10); set('p_ma', 20);
    out.dirtyBefore = document.getElementById('dirtyFlag').style.display;
    document.getElementById('runBtn').click();
    await sleep(300);
    out.dirtyAfter = document.getElementById('dirtyFlag').style.display;
    out.after = { M4: snap('M4'), rows: document.querySelectorAll('#tradeBody tr').length,
                  first3: state.results.M4.trades.slice(0, 3).map(t => [t.buyDate, +t.buyPrice.toFixed(2), t.sellDate, +t.sellPrice.toFixed(2), +t.net.toFixed(2), t.reason]) };
    const series = chart.getOption().series;
    out.markers = { scatter: series.filter(s => s.type === 'scatter').length,
                    scatterPts: series.filter(s => s.type === 'scatter').reduce((a, s) => a + s.data.length, 0),
                    segLines: series.filter(s => s.name && s.name.indexOf('持仓段') >= 0).length,
                    seriesTotal: series.length };
    out.yAxisAfter = { min: chart.getOption().yAxis[2].min, max: chart.getOption().yAxis[2].max };
    out.toast = document.getElementById('toast').textContent;

    document.querySelector('.tab[data-tab="compare"]').click();
    await sleep(150);
    out.compareRows = document.querySelectorAll('#tabCompare tbody tr').length;

    document.querySelector('.tab[data-tab="diag"]').click();
    await sleep(700);
    out.maskOpen = document.getElementById('mask').classList.contains('on');
    out.diagVerdict = document.getElementById('diagVerdict') ? document.getElementById('diagVerdict').textContent.trim().slice(0, 60) : null;
    out.diagLight = document.getElementById('diagLight') ? document.getElementById('diagLight').textContent.trim() : null;
    out.modal = { tr: document.getElementById('mTr').textContent, te: document.getElementById('mTe').textContent,
                  bias: document.getElementById('mBias').textContent, biasSub: document.getElementById('mBias2').textContent };
    // 回填最优参数按钮
    document.getElementById('fillBtn').click();
    await sleep(500);
    out.afterFill = { tp: document.getElementById('p_tp').value, sl: document.getElementById('p_sl').value, maskOpen: document.getElementById('mask').classList.contains('on') };

    document.getElementById('themeBtn').click();
    await sleep(600);
    out.theme = document.documentElement.dataset.theme;
    return JSON.stringify(out);
  })()`;

  const r = await send(ws, 'Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  const probe = await send(ws, 'Runtime.evaluate', { expression: 'JSON.stringify({a:1,st:typeof state,ch:typeof chart})', returnByValue: true });
  console.log('PROBE:', JSON.stringify(probe));
  console.log('RAW_KEYS:', Object.keys(r));
  if (r.exceptionDetails) console.log('EXC:', JSON.stringify(r.exceptionDetails).slice(0, 800));
  console.log('RESULT:', r.result && r.result.value ? r.result.value : JSON.stringify(r));

  await sleep(800);
  const shot = await send(ws, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(SHOT, Buffer.from(shot.data, 'base64'));
  console.log('SHOT:', SHOT, fs.statSync(SHOT).size, 'bytes');

  ws.close();
  child.kill();
  process.exit(0);
})().catch(e => { console.error('ERR', e.message); child.kill(); process.exit(1); });
