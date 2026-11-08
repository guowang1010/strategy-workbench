// CDP 验收：刷新恢复（BUY-10）+ 未成交续期（BUY-08）——关键步骤全部断言（第九轮改造）。
//
// 以前这一步只打印，出现 no / no-row / 导入失败 仍然 EXIT 0，等于没验收。现在：
//   · 每一步点击必须真的点到（mustClick），找不到元素立即失败；
//   · 恢复横幅、保存按钮、续期文案、记录条数、→#N 关联、时间线都要断言；
//   · 导入第二版行情后，续期必须真的产生新记录（as_of 前进到新数据末日）。
//
// 用法：node tools/cdp_rollover_check.js http://127.0.0.1:8766/
// 说明：脚本自己导入两份临时行情（roll-v1 / roll-v2），跑完删掉本轮记录与数据集；
//       不需要「手工干净库」。断言全部按本轮 record_id / dataset_id 核对。
const fs = require('fs');
const os = require('os');
const path = require('path');
const { launch, must, mustInclude, run, waitReady, pickModel, selectDataset } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const SRC = path.resolve(__dirname, '..', 'backend', 'data', 'sample_600186.csv');
// 用**独立股票代码**做续期验收：600186 在「默认样本」里也有一份更长历史，
// 而续期按「该代码的最新数据集」选源，两个数据集会互相干扰（end_date 谁大选谁）。
// 换一个只属于本脚本的代码，才能确定「续期读到的是我刚才导入的那份行情」。
const TEST_CODE = '601999';

function makeVersions() {
  const lines = fs.readFileSync(SRC, 'utf8').replace(/^\uFEFF/, '').trim().split(/\r?\n/);
  const head = lines[0];
  const rows = lines.slice(1).map(line => {
    const c = line.split(',');
    c[2] = TEST_CODE;                       // 股票代码
    c[3] = '验收测试控股';                    // 股票名称
    return c.join(',');
  });
  must(rows.length > 260, '样例数据太短，无法切两版', rows.length);
  // 临时文件目录：必须落在后端允许导入的目录（项目 backend/data）内，
  // 否则 /api/datasets/seed 会以 PATH_FORBIDDEN 拒绝——这不是产品缺陷，是导入白名单。
  const dir = process.env.ROLLOVER_DIR
    || path.join(path.resolve(__dirname, '..', 'backend', 'data'), '_cdp_rollover_tmp_' + Date.now());
  fs.mkdirSync(dir, { recursive: true });
  const v1 = path.join(dir, 'v1.csv');
  const v2 = path.join(dir, 'v2.csv');
  fs.writeFileSync(v1, [head, ...rows.slice(0, 250)].join('\n') + '\n', 'utf8');
  fs.writeFileSync(v2, [head, ...rows].join('\n') + '\n', 'utf8');
  const lastV2 = rows[rows.length - 1].split(',')[1];
  return { v1, v2, lastV2, n: rows.length };
}

const msgText = `(() => { const el = document.querySelector('.toast, .msg, .p-msg');
  return el ? el.textContent.replace(/\\s+/g, ' ').trim() : ''; })()`;

run(async () => {
  // 先清上次失败退出（EXIT 1 走不到末尾清理）留下的临时 CSV 目录。
  const DATA_DIR = path.join(path.resolve(__dirname, '..', 'backend', 'data'));
  for (const ent of (fs.existsSync(DATA_DIR) ? fs.readdirSync(DATA_DIR) : [])) {
    if (/^_cdp_rollover_tmp_\d+$/.test(ent)) {
      try { fs.rmSync(path.join(DATA_DIR, ent), { recursive: true, force: true }); } catch (_) { /* 非关键 */ }
    }
  }
  const { v1, v2, lastV2 } = makeVersions();
  const s = await launch({ port: 9355, url: URL, scale2: true });
  const api = async (p, opt) => JSON.parse(await s.eval(`(async () => {
    const r = await fetch(${JSON.stringify(p)}, ${JSON.stringify(opt || {})});
    return JSON.stringify(await r.json()); })()`));

  // 前置清理：自动清掉上次留下的 roll-v* 数据集，不再要求「手工干净库」。
  // 数据集是不可变快照，残留会让「行情未变 → 不生成新记录」失真：
  // plan_id 含 dataset_id，同名但不同 id 的数据集会算成两套行情。
  const leftover = (await api('/api/datasets')).data.filter(d => /^roll-v/.test(d.name));
  for (const d of leftover) {
    const r = await api(`/api/datasets/${d.id}`, { method: 'DELETE' });
    must(r.ok === true, `清理残留数据集「${d.name}」失败（可能仍被旧计划引用，请先删记录）`, r);
  }
  if (leftover.length) console.log(`已清理残留数据集 ${leftover.length} 个`);

  // 0) 导入短版行情（自己准备数据，不依赖外部脚本先跑过）
  const seed1 = await s.eval(`(async () => {
    const r = await fetch('/api/datasets/seed', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: ${JSON.stringify(v1)}, name: 'roll-v1' }) });
    return JSON.stringify(await r.json()); })()`);
  const seed1j = JSON.parse(seed1);
  must(seed1j.ok === true, '导入 v1 数据集失败', seed1);
  const dsV1 = seed1j.data.dataset_id;

  // 1) 新数据集刚导入，下拉还是页面加载时的旧列表：刷新 → 明确选中 roll-v1 → 明确选中模型。
  //    不依赖「应用默认选中最新数据集」这种隐式行为（那正是 cdp_run_check 空表的根因）。
  await s.send('Page.reload', { ignoreCache: true });
  await waitReady(s);
  await selectDataset(s, { label: 'roll-v1', code: TEST_CODE, minBars: 200 });
  await pickModel(s, '买入持有');
  // 计划面板：手动账户（现金 5 万、空仓）+ 研究模式 —— 有持仓时不会有买入计划，
  // 必须先把账户设成「空仓 + 有现金」才能生成可保存的买入计划
  await s.mustClick('下一笔计划', { within: '.tabs' });
  await s.mustClick('我的实际账户');
  const acct = await s.eval(`(() => {
    const set = (el, v) => {
      const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      d.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true }));
    };
    const ins = [...document.querySelectorAll('.p-acct input')];
    if (ins.length < 3) return 'no-inputs:' + ins.length;
    set(ins[0], '50000'); set(ins[1], '0'); set(ins[2], '0');
    return 'ok'; })()`);
  must(acct === 'ok', '手动账户输入框不可用', acct);
  const on = await s.eval(`(() => {
    const cb = [...document.querySelectorAll('.p-bar-right input[type=checkbox]')][0];
    return cb ? cb.checked : null; })()`);
  must(on !== null, '找不到研究模式复选框');
  if (!on) await s.mustRealClick('.p-bar-right input[type=checkbox]');
  must(await s.eval(`(() => {
    const cb = [...document.querySelectorAll('.p-bar-right input[type=checkbox]')][0];
    return cb ? cb.checked : false; })()`) === true, '研究模式未真正勾选');

  // 2) 生成 + 保存
  await s.mustClick('生成计划');
  await s.waitUntil(`!!document.querySelector('.p-status .p-badge')`, { timeout: 30000, desc: '计划状态徽章' });
  const status = await s.mustText('.p-status .p-badge');
  await s.mustClick('保存计划并跟踪');
  const saved = await s.waitUntil(`(() => {
    const b = [...document.querySelectorAll('.pt-head-right button')].find(x => x.textContent.includes('已保存'));
    return b ? b.textContent.trim() : ''; })()`, { timeout: 30000, desc: '保存成功标记' });
  mustInclude(saved, '#', '保存后应显示记录编号', saved);
  // 本轮记录的编号：后续所有「有没有新增」都按 record_id 判断，
  // 不再数整个跟踪列表——别的验收脚本留下的记录不该让本脚本失败。
  const rid1 = Number((saved.match(/#(\d+)/) || [])[1]);
  must(rid1 > 0, '未能从保存提示中解析记录编号', saved);
  const rec1 = (await api(`/api/plans/${rid1}`)).data.record;
  const code = rec1.code;
  must(rec1.status === (rec1.plan || {}).status,
    '落库的 status 与 plan 快照不一致（F02）', [rec1.status, (rec1.plan || {}).status]);
  const beforeList = (await api(`/api/plans?code=${code}&limit=500`)).data.items;
  must(!beforeList.some(r => r.renewed_from === rid1), '首次保存不可能是续期记录');
  // 研究模式 + 行情落后：执行日/有效期必须标成「历史」，不能像当前待执行订单
  mustInclude(status, '历史情景', '研究模式下计划状态应标为历史情景', status);
  const kvs = await s.eval(`[...document.querySelectorAll('.p-kv')]
    .map(r => r.textContent.replace(/\\s+/g, ' ').trim())`);
  must(kvs.some(t => t.includes('历史执行日') || t.includes('历史检查日')),
    '执行日未标为历史情景', kvs);
  must(kvs.some(t => t.includes('历史有效至') || t.includes('历史有效期')),
    '有效期未标为历史情景', kvs);
  console.log(`生成并保存：${status.trim()} → ${saved}`);

  // 3) 刷新页面：必须从跟踪记录恢复，而不是空白面板
  await s.send('Page.reload', { ignoreCache: true });
  await waitReady(s);
  await s.mustClick('下一笔计划', { within: '.tabs' });
  const restored = await s.waitUntil(`(() => {
    const b = document.querySelector('.p-restored');
    return b ? b.textContent.replace(/\\s+/g, ' ').trim() : ''; })()`,
  { timeout: 20000, desc: '恢复横幅' });
  mustInclude(restored, '已从跟踪记录', '恢复横幅文案异常', restored);
  const statusAfter = await s.mustText('.p-status .p-badge');
  must(!!statusAfter, '刷新后计划状态未恢复');
  console.log('刷新恢复：' + restored.slice(0, 60));
  console.log('截图: ' + await s.shot('ui_plan_restored.png'));

  // 4) 展开跟踪 → 打开记录 → 行情未变时续期应提示「完全一致」
  const hasList = await s.eval(`!!document.querySelector('.pt-list')`);
  if (!hasList) await s.mustClick('展开');
  await s.waitUntil(`document.querySelectorAll('.pt-list .pt-row:not(.pt-hd)').length >= 1`,
    { timeout: 20000, desc: '跟踪列表行' });
  await s.mustEval(`(() => {
    const r = document.querySelector('.pt-list .pt-row:not(.pt-hd)');
    if (!r) return false; r.click(); return true; })()`, '跟踪记录不可点开');
  await s.waitUntil(`!!document.querySelector('.pt-detail')`, { timeout: 20000, desc: '记录详情' });

  await s.mustClick('按最新行情续期');
  const sameMsg = await s.waitUntil(`(() => {
    const t = ${msgText};
    return /未生成新记录|已按最新行情续期到/.test(t) ? t : ''; })()`,
  { timeout: 40000, desc: '续期结果' });
  mustInclude(sameMsg, '未生成新记录', '行情未变时续期不应产生新记录', sameMsg);
  const midList = (await api(`/api/plans?code=${code}&limit=500`)).data.items;
  must(midList.length === beforeList.length, '行情未变时不应新增任何记录',
    [beforeList.length, midList.length]);
  must(!midList.some(r => r.renewed_from === rid1), '行情未变却产生了续期记录');
  const rec1b = (await api(`/api/plans/${rid1}`)).data.record;
  must(rec1b.superseded_by == null, '行情未变时旧记录不应被标记「已被取代」', rec1b.superseded_by);
  console.log('同行情续期：' + sameMsg);

  // 5) 导入更长的一版行情，再续期：必须真的产生新记录
  const seed2 = await s.eval(`(async () => {
    const r = await fetch('/api/datasets/seed', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: ${JSON.stringify(v2)}, name: 'roll-v2' }) });
    return JSON.stringify(await r.json()); })()`);
  const seed2j = JSON.parse(seed2);
  must(seed2j.ok === true, '导入 v2 数据集失败', seed2);
  const dsV2 = seed2j.data.dataset_id;

  await s.send('Page.reload', { ignoreCache: true });
  await waitReady(s);
  await selectDataset(s, { label: 'roll-v2', code: TEST_CODE, minBars: 300 });
  await pickModel(s, '买入持有');
  await s.mustClick('下一笔计划', { within: '.tabs' });
  await s.waitUntil(`document.querySelectorAll('.pt-list .pt-row:not(.pt-hd)').length >= 1`,
    { timeout: 20000, desc: '刷新后的跟踪列表' });
  await s.mustEval(`(() => {
    const r = document.querySelector('.pt-list .pt-row:not(.pt-hd)');
    if (!r) return false; r.click(); return true; })()`, '跟踪记录不可点开');
  await s.waitUntil(`!!document.querySelector('.pt-detail')`, { timeout: 20000, desc: '记录详情' });

  await s.mustClick('按最新行情续期');
  const newMsg = await s.waitUntil(`(() => {
    const t = ${msgText};
    return /已按最新行情续期到/.test(t) ? t : ''; })()`,
  { timeout: 60000, desc: '续期到新记录' });
  mustInclude(newMsg, '已按最新行情续期到', '续期未产生新记录', newMsg);
  mustInclude(newMsg, lastV2, '续期应基于最新行情日期', newMsg);
  const rid2 = Number((newMsg.match(/#(\d+)/) || [])[1]);
  must(rid2 > 0, '未能从续期提示中解析新记录编号', newMsg);
  must(rid2 !== rid1, '续期应生成新记录，而不是复用原记录', [rid1, rid2]);

  // 业务事实按 record_id 核对，避免「列表里有几条」这种会被历史数据影响的断言
  const rec2 = (await api(`/api/plans/${rid2}`)).data.record;
  must(rec2.renewed_from === rid1, '续期记录的 renewed_from 应指向原记录', rec2.renewed_from);
  must(rec2.as_of === lastV2, '续期记录的 as_of 应为最新行情日', [rec2.as_of, lastV2]);
  must(rec2.dataset_id !== rec1.dataset_id, '续期应基于新导入的数据集',
    [rec1.dataset_id, rec2.dataset_id]);
  must(rec2.status === (rec2.plan || {}).status, '新记录 status 与快照不一致（F02）',
    [rec2.status, (rec2.plan || {}).status]);
  const rec1c = (await api(`/api/plans/${rid1}`)).data.record;
  must(rec1c.superseded_by === rid2, '旧记录应标注「已被 #新编号 取代」', rec1c.superseded_by);

  const rows = await s.eval(`[...document.querySelectorAll('.pt-list .pt-row:not(.pt-hd)')]
    .map(r => r.textContent.replace(/\\s+/g, ' ').trim())`);
  must(rows.some(r => r.includes('#' + rid1) && r.includes('→#' + rid2)),
    '列表里旧记录应标注「→新编号」', rows);
  const detail = await s.text('.pt-detail');
  mustInclude(detail, '续期来源', '详情应展示续期来源');
  const tl = await s.eval(`[...document.querySelectorAll('.pt-tl li')].map(x => x.textContent.trim())`);
  must(tl.some(x => x.includes('续期')), '时间线应记录续期事件', tl);
  console.log('续期到新记录：' + newMsg);
  console.log('截图: ' + await s.shot('ui_plan_superseded.png'));

  // 6) 清理**本轮**两条记录（按 id 删，不碰别人的记录）
  for (const id of [rid2, rid1]) {
    const del = await api(`/api/plans/${id}`, { method: 'DELETE' });
    must(del.ok === true, `删除演示记录 #${id} 失败`, del);
  }
  const endList = (await api(`/api/plans?code=${code}&limit=500`)).data.items;
  must(!endList.some(r => r.id === rid1 || r.id === rid2),
    '演示记录未清理干净', endList.map(r => r.id));

  // 7) 清理本轮导入的两个数据集，保证下次运行仍是「干净起点」
  for (const id of [dsV2, dsV1]) {
    const r = await api(`/api/datasets/${id}`, { method: 'DELETE' });
    must(r.ok === true, `清理测试数据集 #${id} 失败`, r);
  }
  try { fs.rmSync(path.dirname(v1), { recursive: true, force: true }); } catch (_) { /* 非关键 */ }

  s.assertClean();
  s.close();
});
