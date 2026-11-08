// CDP 验收：跨股票汇总（第九轮改造）——含样本外要断言到「请求参数 + 表头 + 灯」，不能只看有没有报错。
//
// 关键改动：
//   · 勾选样本外改为**真实鼠标点击**（React 受控组件在 headless 下，直接改 checked 派发 change
//     有时不会触发 onChange，导致「看起来勾上了、请求里还是 false」）；
//   · 点击后必须断言复选框真的变为 checked；
//   · 断言发往 /api/jobs/summary 的请求体里 include_oos=true；
//   · 断言任务完成、表头出现「样本外%」「灯」、七只个股的灯都渲染出来（不是 '…' 占位）。
const { launch, must, mustInclude, run, waitReady, selectDataset } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const LAMP = '/[\\u{1F534}\\u{1F7E1}\\u{1F7E2}]/u';

run(async () => {
  const s = await launch({ port: 9349, url: URL, scale2: true });

  // 显式选 7 只、300 日的默认样本：默认可能是 28 根的新股，
  // 那样 M4 样本不足，样本外只能显示「…」，汇总验收会变成假失败。
  await waitReady(s);
  await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });

  await s.mustClick('跨股票汇总', { within: '.tabs' });

  // ---- 第一轮：不含样本外 ----
  await s.mustClick('生成跨股票汇总');
  await s.waitUntil(`(() => {
    const m = document.querySelector('.msg')?.textContent || '';
    if (m.includes('汇总失败')) return 'fail:' + m;
    return m.includes('汇总完成') ? 'ok' : ''; })()`,
  { timeout: 180000, interval: 800, desc: '汇总完成' }).then(v => must(v === 'ok', '汇总任务未成功', v));

  const base = await s.eval(`(() => {
    const t = document.querySelector('.summary-table');
    if (!t) return { rows: 0, heads: [], cells: 0 };
    return {
      rows: t.querySelectorAll('tbody tr').length,
      heads: [...t.querySelectorAll('thead th')].map(x => x.textContent.trim()),
      cells: t.querySelectorAll('tbody td').length,
    }; })()`);
  must(base.rows >= 1, '汇总表没有数据行', base);
  must(base.cells >= base.rows * 3, '汇总表单元格数异常', base);
  must(!base.heads.some(h => h.includes('样本外')), '未勾选样本外时不应出现样本外列', base.heads);
  console.log(`基础汇总：${base.rows} 行 × ${base.heads.length} 列`);
  console.log('截图: ' + await s.shot('ui_summary.png'));

  // ---- 第二轮：真实点击勾选样本外 ----
  const before = await s.eval(`(() => {
    const c = document.querySelector('.summary-bar input[type=checkbox]');
    return c ? c.checked : null; })()`);
  must(before !== null, '找不到样本外复选框（.summary-bar input[type=checkbox]）');
  if (before) {                       // 已勾上则先取消，保证这次是「点出来的」
    await s.mustRealClick('.summary-bar input[type=checkbox]');
  }
  await s.mustRealClick('.summary-bar input[type=checkbox]');
  const after = await s.eval(`document.querySelector('.summary-bar input[type=checkbox]').checked`);
  must(after === true, '真实点击后复选框仍未勾选（受控组件没收到点击）');

  const mark = s.requests.length;
  await s.mustClick('生成跨股票汇总');
  await s.waitUntil(`(() => {
    const m = document.querySelector('.msg')?.textContent || '';
    if (m.includes('汇总失败')) return 'fail:' + m;
    return m.includes('汇总完成') ? 'ok' : ''; })()`,
  { timeout: 300000, interval: 1000, desc: '含样本外的汇总完成' })
    .then(v => must(v === 'ok', '含样本外的汇总任务未成功', v));

  // 请求体必须带 include_oos=true（这一步能抓到「界面勾了、请求没带」的假通过）
  const req = s.requests.slice(mark).find(r => r.url.includes('/api/jobs/summary'));
  must(!!req, '没有发出 /api/jobs/summary 请求', s.requests.slice(mark).map(r => r.url));
  mustInclude(req.postData || '', '"include_oos":true', '汇总请求未携带 include_oos=true');

  const oos = await s.eval(`(() => {
    const t = document.querySelector('.summary-table'); if (!t) return null;
    const heads = [...t.querySelectorAll('thead th')].map(x => x.textContent.trim());
    const tds = [...t.querySelectorAll('tbody td')];
    const verdicts = tds.filter(td => ${LAMP}.test(td.textContent)).map(td => td.textContent.trim());
    const pending = tds.filter(td => td.textContent.trim() === '…').length;
    return { heads, verdicts, pending, rows: t.querySelectorAll('tbody tr').length }; })()`);
  must(oos, '含样本外结果缺少表格');
  must(oos.heads.some(h => h.includes('样本外')), '表头缺少「样本外%」列', oos.heads);
  must(oos.heads.some(h => h.includes('灯')), '表头缺少「灯」列', oos.heads);
  must(oos.verdicts.length >= oos.rows, '红绿灯未渲染完整', oos);
  must(oos.pending === 0, '仍有「…」占位单元格，样本外没算完', oos);

  console.log(`样本外：${oos.verdicts.length} 个灯 ${oos.verdicts.join(' ')}`);
  console.log('截图: ' + await s.shot('ui_summary_oos.png'));
  s.assertClean();
  s.close();
});
