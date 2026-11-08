// CDP 验收公共库（第九轮）：启动 headless Edge、连接、求值、**断言**、截图。
//
// 为什么要有断言：以前的脚本只打印值，人工看完认为「没报错 = 通过」。
// 结果是 no-el / no-row / heat=0 / 导入失败 都能 EXIT 0，验收形同虚设。
// 现在关键步骤一律 must()，失败立即 throw → 退出码 1；业务结果（记录数、文案、
// 请求参数、表头）也要断言，而不是只看「有没有 JS 异常」。
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const EDGE = process.env.EDGE_PATH
  || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

const sleep = ms => new Promise(r => setTimeout(r, ms));

class AssertError extends Error {
  constructor(msg, detail) {
    const d = detail === undefined ? ''
      : ' | ' + (typeof detail === 'string' ? detail : JSON.stringify(detail));
    super(msg + d);
    this.name = 'AssertError';
  }
}

/** 硬断言：不满足就抛错（退出码 1），不允许「打印一下继续跑」 */
function must(cond, msg, detail) {
  if (!cond) throw new AssertError('断言失败：' + msg, detail);
  return cond;
}

function mustInclude(haystack, needle, msg) {
  return must(typeof haystack === 'string' && haystack.includes(needle),
    msg || `应包含「${needle}」`, haystack);
}

function mustNotInclude(haystack, needle, msg) {
  return must(!(typeof haystack === 'string' && haystack.includes(needle)),
    msg || `不应包含「${needle}」`, haystack);
}

async function getTarget(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await r.json();
      const t = list.find(x => x.type === 'page' && x.url.startsWith('http'));
      if (t && t.webSocketDebuggerUrl) return t;
    } catch (e) { /* 还没起来 */ }
    await sleep(250);
  }
  throw new Error(`CDP 目标未就绪（端口 ${port}）：Edge 没启动或调试端口被占用`);
}

function connect(url) {
  return new Promise((res, rej) => {
    const ws = new WebSocket(url);
    ws.onopen = () => res(ws);
    ws.onerror = () => rej(new Error('WebSocket 连接失败'));
  });
}

/**
 * 启动并连接一个 headless Edge 会话。
 * @returns {Promise<object>} session：eval / mustEval / click / mustClick / realClick /
 *   text / count / waitFor / shot / requests / errors / close
 */
async function launch(opts = {}) {
  const port = opts.port || 9350;
  const url = opts.url || 'http://127.0.0.1:8765/';
  const width = opts.width || 1600;
  const height = opts.height || 1100;
  const outDir = opts.outDir || path.resolve(__dirname, '..', 'frontend');
  const settle = opts.settle == null ? 4000 : opts.settle;

  const prof = path.join(os.tmpdir(), `edge_cdp_${port}_${Date.now()}`);
  const args = [
    '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
    '--remote-debugging-port=' + port, '--user-data-dir=' + prof,
    `--window-size=${width},${height}`,
  ];
  if (opts.scale2) args.push('--force-device-scale-factor=2');
  args.push(url);
  const child = spawn(EDGE, args, { stdio: 'ignore' });

  const errors = [];
  const requests = [];
  let msgId = 0;
  const pending = new Map();
  let ws;

  const send = (method, params = {}) => {
    const id = ++msgId;
    return new Promise((res, rej) => {
      pending.set(id, { res, rej });
      ws.send(JSON.stringify({ id, method, params }));
    });
  };

  const evalJs = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) {
      throw new Error('页面求值抛错：'
        + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    }
    return r.result?.value;
  };

  // 点击：先按选择器，再按按钮文字（精确优先，其次包含）
  // within: 限定容器选择器（例如 '.tabs'）——顶部动作按钮与 Tab 同名时，必须点 Tab
  const click = async (selectorOrText, { selector = false, within = '' } = {}) => {
    const scope = within ? `${within} button` : 'button, .tabs button';
    const expr = selector
      ? `(() => { const el = document.querySelector(${JSON.stringify(selectorOrText)});
           if (!el) return 'no-el'; el.click(); return 'ok'; })()`
      : `(() => {
           const txt = ${JSON.stringify(selectorOrText)};
           const all = [...document.querySelectorAll(${JSON.stringify(scope)})];
           const b = all.find(x => x.textContent.trim() === txt)
             || all.find(x => x.textContent.includes(txt));
           if (!b) return 'no-btn'; b.click(); return 'ok'; })()`;
    const r = await evalJs(expr);
    if (r !== 'ok') return r;
    await sleep(opts.clickSettle || 400);
    return 'ok';
  };

  const realClick = async (selector) => {
    const rect = await evalJs(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    })()`);
    if (!rect) return 'no-el';
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: rect.x, y: rect.y });
    await send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: rect.x, y: rect.y, button: 'left', clickCount: 1,
    });
    await send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: rect.x, y: rect.y, button: 'left', clickCount: 1,
    });
    await sleep(300);
    return 'ok';
  };

  const text = sel => evalJs(`(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    return el ? el.textContent.replace(/\\s+/g, ' ').trim() : ''; })()`);

  const count = sel => evalJs(`document.querySelectorAll(${JSON.stringify(sel)}).length`);

  const waitUntil = async (expr, { timeout = 60000, interval = 500, desc = '条件' } = {}) => {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
      last = await evalJs(expr);
      if (last) return last;
      await sleep(interval);
    }
    throw new AssertError(`等待超时（${timeout}ms）：${desc}`, last);
  };

  const shot = async (file, captureBeyondViewport = true) => {
    const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport });
    const data = r && (r.data || (r.result && r.result.data));
    if (!data) throw new Error('截图失败：没有返回数据');
    const full = path.isAbsolute(file) ? file : path.join(outDir, file);
    fs.writeFileSync(full, Buffer.from(data, 'base64'));
    return full;
  };

  const target = await getTarget(port, opts.targetTimeout || 25000);
  ws = await connect(target.webSocketDebuggerUrl);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description
        || m.params.exceptionDetails.text);
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push('[console.error] '
        + m.params.args.map(a => a.value ?? a.description).join(' '));
    }
    if (m.method === 'Network.requestWillBeSent') {
      const req = m.params.request;
      requests.push({ url: req.url, method: req.method, postData: req.postData || '' });
    }
    if (m.method === 'Network.responseReceived' && m.params.response.status >= 400) {
      const u = m.params.response.url || '';
      // favicon 之类的静态缺省请求不算功能失败（浏览器总会去要一次）
      if (!/\/favicon\.ico$/.test(u)) {
        errors.push(`[http ${m.params.response.status}] ${u}`);
      }
    }
    if (m.method === 'Page.javascriptDialogOpening') {
      send('Page.handleJavaScriptDialog', { accept: true }).catch(() => {});
    }
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Network.enable');
  if (settle) await sleep(settle);

  return {
    send, eval: evalJs, click, realClick, text, count, waitUntil, shot,
    requests, errors,
    mustEval: async (expr, msg) => {
      const v = await evalJs(expr);
      must(v, msg, v);
      return v;
    },
    mustClick: async (label, opt) => {
      const r = await click(label, opt);
      must(r === 'ok', `按钮不可点击「${label}」`, r);
      return r;
    },
    mustRealClick: async (sel) => {
      const r = await realClick(sel);
      must(r === 'ok', `元素不可点击 ${sel}`, r);
      return r;
    },
    mustText: async (sel, msg) => {
      const t = await text(sel);
      must(!!t, msg || `元素无文本：${sel}`, sel);
      return t;
    },
    /** 结束时统一检查：JS 异常 / console.error / 4xx 一律算失败 */
    // allowHttp：豁免「预期中的失败响应」（如故意触发 502 的失败路径测试）
    assertClean: (allowHttp = []) => must(
      errors.filter(e => !allowHttp.some(p => e.includes(p))).length === 0,
      '存在运行期错误', errors.slice(0, 5)),
    close: () => {
      try { ws.close(); } catch (e) { /* ignore */ }
      try { child.kill(); } catch (e) { /* ignore */ }
    },
  };
}

/** 统一入口：失败打印原因并退出 1（退出码即验收结论） */
function run(main) {
  main().then(() => {
    console.log('PASS');
    process.exit(0);
  }).catch((e) => {
    console.error((e.name === 'AssertError' ? '✗ ' : '✗ 异常 ') + e.message);
    if (e.stack && e.name !== 'AssertError') console.error(e.stack.split('\n').slice(1, 4).join('\n'));
    process.exit(1);
  });
}

/**
 * 选定并展示某个模型（按 .mname 精确匹配，避免「止盈止损」误中「自定义规则(止盈止损)」）。
 * 不依赖上一个脚本的遗留状态：勾选 → 等待 React 提交 → 点卡片 → 断言 active。
 */
async function pickModel(s, name, { check = true } = {}) {
  const r = await s.eval(`(() => {
    const cards = [...document.querySelectorAll('.model-card')];
    const card = cards.find(c => (c.querySelector('.mname')?.textContent || '').trim() === ${JSON.stringify(name)});
    if (!card) return 'no-card:' + cards.map(c => (c.querySelector('.mname')?.textContent || '').trim()).join('|');
    const cb = card.querySelector('input[type=checkbox]');
    if (${check} && cb && !cb.checked) { cb.click(); return 'checked'; }
    return 'ready';
  })()`);
  must(!String(r).startsWith('no-card'), `模型卡未找到：${name}`, r);
  if (r === 'checked') await sleep(300);          // 等 React 把 selected 写进 store
  const a = await s.eval(`(() => {
    const cards = [...document.querySelectorAll('.model-card')];
    const card = cards.find(c => (c.querySelector('.mname')?.textContent || '').trim() === ${JSON.stringify(name)});
    if (!card) return 'no-card';
    card.click(); return 'ok';
  })()`);
  must(a === 'ok', `模型卡不可点击：${name}`, a);
  await sleep(350);
  const active = await s.eval(`(document.querySelector('.model-card.active .mname') || {}).textContent || ''`);
  must(active.trim() === name, `当前展示模型不是「${name}」`, active);
  return active.trim();
}

/**
 * 页面就绪前置条件：模型卡、数据集/个股下拉、K 线全部到位。
 * 各脚本必须先过这一关再断言业务结果——固定 sleep 是「PRE cards=0 仍 EXIT 0」的根因。
 */
async function waitReady(s, { model = null } = {}) {
  await s.waitUntil(`document.querySelectorAll('.model-card').length > 0`,
    { desc: '模型卡片渲染（init 未完成）', timeout: 60000 });
  await s.waitUntil(`(() => {
    const sel = [...document.querySelectorAll('.topbar select')];
    return sel.length >= 2 && sel[0].options.length > 0 && !!sel[0].value && !!sel[1].value;
  })()`, { desc: '数据集/个股下拉就绪', timeout: 60000 });
  await s.waitUntil(`!!(window.__chart && window.__chart.getOption().series.length)`,
    { desc: 'K 线数据加载', timeout: 60000 });
  if (model) await pickModel(s, model);
}

/**
 * 显式切换数据集 + 个股，并等到该股票真的有足够历史数据。
 * 为什么必须：应用默认选中「最新导入的数据集」——可能是 28 根的新股（601091），
 * 拿它跑 M4 必然 0 交易；旧脚本把这种「业务上正确但没样本」当成通过/失败都不对。
 */
async function selectDataset(s, { label = '默认样本', code = '600186', minBars = 100 } = {}) {
  const r = await s.eval(`(() => {
    const sel = document.querySelectorAll('.topbar select')[0];
    if (!sel) return 'no-select';
    const opt = [...sel.options].find(o => o.textContent.includes(${JSON.stringify(label)}));
    if (!opt) return 'no-ds:' + [...sel.options].map(o => o.textContent).join('|');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
    setter.call(sel, opt.value);
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    return 'ok:' + opt.value;
  })()`);
  must(!String(r).startsWith('no-'), `数据集切换失败：${label}`, r);
  await s.waitUntil(`(() => {
    const cs = document.querySelectorAll('.topbar select')[1];
    return !!cs && [...cs.options].some(o => o.value === ${JSON.stringify(code)});
  })()`, { desc: `个股下拉出现 ${code}`, timeout: 30000 });
  if (code) {
    const c = await s.eval(`(() => {
      const sel = document.querySelectorAll('.topbar select')[1];
      if (!sel) return 'no-code';
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
      setter.call(sel, ${JSON.stringify(code)});
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      return 'ok';
    })()`);
    must(c === 'ok', `个股切换失败：${code}`, c);
  }
  await s.waitUntil(`(() => {
    const ch = window.__chart;
    if (!ch) return false;
    const o = ch.getOption();
    return o.series.length > 0 && (o.series[0].data || []).length >= ${minBars};
  })()`, { desc: `${code} 历史数据加载（≥${minBars} 根）`, timeout: 30000 });

  // 等「刷新恢复计划」落定：restorePlan 可能把当前展示模型改成旧记录里的模型（如 M1），
  // 如果它在 pickModel 之后才回来，点 Tab/生成计划就会用错模型——这正是
  // 「该模型无默认搜索空间」的由来。这里等 /api/plans/last 返回再继续。
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (s.requests.some(r => /\/api\/plans\/last/.test(r.url))) break;
    await sleep(100);
  }
  await sleep(400);
}

module.exports = {
  launch, must, mustInclude, mustNotInclude, sleep, run, AssertError, EDGE,
  pickModel, waitReady, selectDataset,
};
