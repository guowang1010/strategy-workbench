// 验收：① 在线拉取行情弹窗（多选代码 + 时间区间）② K 线区折叠 + 副图开关
// 用法：node tools/cdp_crawl_fold_check.js [url]
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const PORT = 9430 + Math.floor(Math.random() * 8);
const prof = path.join(os.tmpdir(), 'edge_crawlfold_' + Date.now());

const child = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + prof,
  '--window-size=1600,1200', URL
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
  // React 受控组件需要真实鼠标事件
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

  await send('Runtime.enable');
  await sleep(5000);

  // ---------- 1) 拉取行情弹窗 ----------
  console.log('1) 打开拉取弹窗:', await evalJs(`(() => {
    const b = [...document.querySelectorAll('.topbar button')].find(x => x.textContent.includes('拉取行情'));
    if (!b) return 'no-btn'; b.click(); return 'clicked';
  })()`));
  await sleep(800);

  const modal = await evalJs(`(() => {
    const m = document.querySelector('.modal');
    if (!m) return JSON.stringify({ open: false });
    return JSON.stringify({
      open: true,
      title: (m.querySelector('.modal-title')||{}).textContent,
      hasCodeInput: !!m.querySelector('.crawl-codes input'),
      modes: [...m.querySelectorAll('.crawl-range .chk')].map(l => l.textContent.trim()),
      dateInputs: m.querySelectorAll('.crawl-range input[type=date]').length,
      daysInput: !!m.querySelector('.crawl-range input[type=number]'),
    });
  })()`);
  console.log('2) 弹窗结构:', modal);

  // 输入 2 只代码并回车 → chips
  await evalJs(`(() => {
    const inp = document.querySelector('.crawl-codes input');
    inp.focus();
    return 'focus';
  })()`);
  await send('Input.insertText', { text: '600519, 000858' });
  await sleep(300);
  await evalJs(`(() => {
    const inp = document.querySelector('.crawl-codes input');
    inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    return 'enter';
  })()`);
  await sleep(500);
  console.log('3) 代码 chips:', await evalJs(`(() => {
    const chips = [...document.querySelectorAll('.chip')].map(c => c.textContent.replace('✕','').trim());
    return JSON.stringify({ count: chips.length, chips });
  })()`));

  // 切到「起止日期」模式，确认日期框可编辑
  const rangeMode = await evalJs(`(async () => {
    const radios = [...document.querySelectorAll('.crawl-range input[type=radio]')];
    radios[1].click();
    await new Promise(r => setTimeout(r, 300));
    const ds = [...document.querySelectorAll('.crawl-range input[type=date]')];
    return JSON.stringify({ checked: radios[1].checked, dates: ds.map(d => ({ v: d.value, disabled: d.disabled })) });
  })()`);
  console.log('4) 区间模式切换:', rangeMode);

  // 切回「最近交易日数」并用小样本拉取（真实网络，可能失败亦需优雅提示）
  // 注：React 受控 input 必须走原生 value setter，否则 _valueTracker 判定未变化、onChange 不触发
  await evalJs(`(async () => {
    const radios = [...document.querySelectorAll('.crawl-range input[type=radio]')];
    radios[0].click();
    await new Promise(r => setTimeout(r, 200));
    const n = document.querySelector('.crawl-range input[type=number]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(n, '60');
    n.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 300));
    return n.value;
  })()`);
  await sleep(400);
  console.log('4b) 已设为最近 N 日:', await evalJs(`document.querySelector('.crawl-range input[type=number]').value`));
  console.log('5) 开始拉取:', await evalJs(`(() => {
    const b = [...document.querySelectorAll('.crawl-foot button')].find(x => x.textContent.includes('开始拉取'));
    if (!b) return 'no-btn';
    b.click(); return 'clicked';
  })()`));

  // 最多等 90 秒
  let msg = '';
  for (let i = 0; i < 90; i++) {
    await sleep(1000);
    msg = await evalJs(`(() => {
      const m = document.querySelector('.msg');
      return m ? m.textContent : '';
    })()`);
    if (msg && !msg.includes('正在拉取')) break;
  }
  console.log('6) 拉取结果提示:', msg);
  console.log('7) 数据集下拉:', await evalJs(`(() => {
    const sels = [...document.querySelectorAll('.topbar select')];
    return JSON.stringify(sels.map(s => [...s.options].map(o => o.textContent).slice(0, 4)));
  })()`));

  // 结果面板应留在弹窗里展示，再点「完成」关闭
  console.log('7b) 结果面板:', await evalJs(`(() => {
    const r = document.querySelector('.crawl-result');
    if (!r) return 'no-result-panel';
    return JSON.stringify({
      head: (r.querySelector('.crawl-res-head')||{}).textContent?.slice(0, 80),
      rows: r.querySelectorAll('.crawl-table tbody tr').length,
    });
  })()`));
  console.log('7c) 关闭弹窗:', await evalJs(`(() => {
    const b = [...document.querySelectorAll('.crawl-foot button')].find(x => x.textContent.trim() === '完成' || x.textContent.trim() === '取消');
    if (!b) return 'no-btn'; b.click();
    return 'closed';
  })()`));
  await sleep(600);
  console.log('7d) 弹窗已卸载:', await evalJs(`document.querySelectorAll('.modal').length === 0`));

  // ---------- 2) K 线折叠 ----------
  await sleep(1500);
  const before = await evalJs(`(() => {
    const card = document.querySelector('.chart-card');
    const body = document.querySelector('.chart-body');
    const tab = document.querySelector('.tab-body');
    return JSON.stringify({ collapsed: card.classList.contains('collapsed'),
      chartH: Math.round(body.getBoundingClientRect().height),
      tabH: Math.round(tab.getBoundingClientRect().height) });
  })()`);
  console.log('8) 折叠前:', before);
  console.log('9) 点击折叠:', await realClick('.chart-bar .fold'));
  await sleep(900);
  const after = await evalJs(`(() => {
    const card = document.querySelector('.chart-card');
    const body = document.querySelector('.chart-body');
    const tab = document.querySelector('.tab-body');
    return JSON.stringify({ collapsed: card.classList.contains('collapsed'),
      chartH: Math.round(body.getBoundingClientRect().height),
      tabH: Math.round(tab.getBoundingClientRect().height),
      sub: (document.querySelector('.chart-sub')||{}).textContent });
  })()`);
  console.log('10) 折叠后:', after);

  console.log('11) 快捷键 C 切换:', await evalJs(`(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', bubbles: true }));
    await new Promise(r => setTimeout(r, 600));
    const body = document.querySelector('.chart-body');
    return JSON.stringify({ collapsed: document.querySelector('.chart-card').classList.contains('collapsed'),
      chartH: Math.round(body.getBoundingClientRect().height) });
  })()`));
  await sleep(600);
  // 保证后续副图测试处于「已展开」状态
  await evalJs(`(async () => {
    if (document.querySelector('.chart-card').classList.contains('collapsed')) {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', bubbles: true }));
      await new Promise(r => setTimeout(r, 600));
    }
    return 'expanded';
  })()`);
  await sleep(500);
  console.log('11b) 恢复到展开:', await evalJs(`JSON.stringify({ collapsed: document.querySelector('.chart-card').classList.contains('collapsed'), chk: document.querySelectorAll('.chart-bar .chk').length })`));

  // ---------- 3) 副图开关 ----------
  const series0 = await evalJs(`(() => {
    const c = window.__chart; if (!c) return 'no-chart';
    const o = c.getOption();
    return JSON.stringify({ grids: o.grid.length, series: o.series.map(s => s.name) });
  })()`);
  console.log('12) 默认副图:', series0);

  const volBox = await evalJs(`(() => {
    const labels = [...document.querySelectorAll('.chart-bar .chk')];
    const l = labels.find(x => x.textContent.includes('成交量'));
    if (!l) return null;
    const r = l.querySelector('input').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (volBox) {
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: volBox.x, y: volBox.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: volBox.x, y: volBox.y, button: 'left', clickCount: 1 });
  }
  await sleep(1200);
  console.log('13) 关闭成交量后:', await evalJs(`(() => {
    const c = window.__chart; const o = c.getOption();
    return JSON.stringify({ grids: o.grid.length, series: o.series.map(s => s.name),
      gridHeights: o.grid.map(g => g.height) });
  })()`));

  const eqBox = await evalJs(`(() => {
    const labels = [...document.querySelectorAll('.chart-bar .chk')];
    const l = labels.find(x => x.textContent.includes('权益曲线'));
    if (!l) return null;
    const r = l.querySelector('input').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (eqBox) {
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: eqBox.x, y: eqBox.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: eqBox.x, y: eqBox.y, button: 'left', clickCount: 1 });
  }
  await sleep(1200);
  console.log('14) 再关闭权益后:', await evalJs(`(() => {
    const c = window.__chart; const o = c.getOption();
    return JSON.stringify({ grids: o.grid.length, series: o.series.map(s => s.name),
      gridHeights: o.grid.map(g => g.height) });
  })()`));

  console.log('15) 刷新后偏好是否保留:', await evalJs(`localStorage.getItem('wb.chart.pref')`));

  console.log('16) JS 异常:', errors.length ? errors : '无');
  ws.close(); child.kill(); process.exit(0);
})().catch(e => { console.error('ERR', e.message); child.kill(); process.exit(1); });
