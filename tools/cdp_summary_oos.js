// CDP 验收（聚焦）：跨股票汇总的「样本外」开关——真实点击 + 请求参数 + 表头 + 灯，全部断言。
//
// 与 cdp_summary_check.js 的区别：只跑含样本外这一条路径，用于快速回归。
// 判定标准：勾选真的生效、请求真的带 include_oos=true、任务真的完成、
// 表头出现「样本外% / 灯」、灯不再停留在 '…'。任一不满足 → EXIT 1。
const { launch, must, mustInclude, run, waitReady, selectDataset } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const LAMP = '/[\\u{1F534}\\u{1F7E1}\\u{1F7E2}]/u';

run(async () => {
  const s = await launch({ port: 9352, url: URL });

  await waitReady(s);
  await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });

  await s.mustClick('跨股票汇总', { within: '.tabs' });

  const before = await s.eval(`(() => {
    const c = document.querySelector('.summary-bar input[type=checkbox]');
    return c ? c.checked : null; })()`);
  must(before !== null, '找不到样本外复选框');
  if (before) await s.mustRealClick('.summary-bar input[type=checkbox]');
  await s.mustRealClick('.summary-bar input[type=checkbox]');
  const after = await s.eval(`document.querySelector('.summary-bar input[type=checkbox]').checked`);
  must(after === true, '真实点击后复选框仍未勾选');

  const mark = s.requests.length;
  await s.mustClick('生成跨股票汇总');
  await s.waitUntil(`(() => {
    const m = document.querySelector('.msg')?.textContent || '';
    if (m.includes('汇总失败')) return 'fail:' + m;
    return m.includes('汇总完成') ? 'ok' : ''; })()`,
  { timeout: 300000, interval: 1000, desc: '含样本外汇总完成' })
    .then(v => must(v === 'ok', '含样本外汇总未成功', v));

  const req = s.requests.slice(mark).find(r => r.url.includes('/api/jobs/summary'));
  must(!!req, '没有发出 /api/jobs/summary 请求');
  mustInclude(req.postData || '', '"include_oos":true', '请求未携带 include_oos=true');

  const t = await s.eval(`(() => {
    const tb = document.querySelector('.summary-table'); if (!tb) return null;
    const heads = [...tb.querySelectorAll('thead th')].map(x => x.textContent.trim());
    const tds = [...tb.querySelectorAll('tbody td')];
    return {
      heads,
      lamps: tds.filter(td => ${LAMP}.test(td.textContent)).map(td => td.textContent.trim()),
      pending: tds.filter(td => td.textContent.trim() === '…').length,
      rows: tb.querySelectorAll('tbody tr').length }; })()`);
  must(t, '缺少汇总表格');
  must(t.heads.some(h => h.includes('样本外')), '表头缺少「样本外%」', t.heads);
  must(t.heads.some(h => h.includes('灯')), '表头缺少「灯」', t.heads);
  must(t.lamps.length >= t.rows, '红绿灯数量不足', t);
  must(t.pending === 0, '仍有「…」占位，样本外未算完', t);

  console.log(`行=${t.rows} 灯=${t.lamps.join(' ')}`);
  console.log('截图: ' + await s.shot('ui_summary_oos.png', false));
  s.assertClean();
  s.close();
});
