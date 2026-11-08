// CDP 端到端验收：初始化 → 运行回测 → 断言「图上有买卖标记 / 交易表有行 / KPI 有数值」→ 切模型对比 → 暗色
//
// 为什么以前「PRE cards=0、POST 散点 0、空表」还能 EXIT 0：
// 脚本只打印不判断。现在每个业务结果都是 must()，失败退出码 1。
// 前置条件不靠固定 sleep，而是 waitUntil 等模型卡 / 下拉 / K 线真正就绪。
const path = require('path');
const { launch, run, must, mustInclude, sleep, waitReady, selectDataset, pickModel } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';

run(async () => {
  const s = await launch({ url: URL, port: 9347, width: 1600, height: 1000, settle: 800 });
  try {
    await waitReady(s);
    // 默认数据集可能是 28 根的新股，跑不出交易——显式选 2100 根的默认样本 + 600186
    await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });

    const pre = JSON.parse(await s.eval(`JSON.stringify({
      cards: document.querySelectorAll('.model-card').length,
      checked: document.querySelectorAll('.model-card input[type=checkbox]:checked').length,
      ds: document.querySelectorAll('.topbar select')[0]?.value || '',
      code: document.querySelectorAll('.topbar select')[1]?.value || '',
      bars: window.__chart.getOption().series.length,
    })`));
    console.log('PRE ' + JSON.stringify(pre));
    must(pre.cards >= 6, '模型卡片数量异常（页面没初始化完？）', pre);
    must(pre.checked >= 1, '默认没有任何模型被勾选，回测请求会为空', pre);
    must(!!pre.ds && !!pre.code, '数据集 / 个股未选中', pre);
    must(pre.bars > 0, 'K 线图没有数据系列', pre);

    await s.mustClick('运行回测');
    await s.waitUntil(`/完成：|回测失败/.test((document.querySelector('.msg') || {}).textContent || '')`,
      { desc: '回测完成提示', timeout: 90000 });

    const post = JSON.parse(await s.eval(`(() => {
      const o = window.__chart.getOption();
      const sc = o.series.filter(x => x.type === 'scatter');
      const pts = sc.reduce((a, x) => a + (x.data ? x.data.length : 0), 0);
      const rows = document.querySelectorAll('.trade-table tbody tr').length;
      const empty = !!document.querySelector('.trade-table td.empty');
      const msg = (document.querySelector('.msg') || {}).textContent || '';
      return JSON.stringify({
        scatter_series: sc.length, scatter_points: pts, table_rows: rows,
        empty_row: empty, msg,
        stats: (document.querySelector('.stats') || {}).textContent || '',
      });
    })()`));
    console.log('POST ' + JSON.stringify(post));
    mustInclude(post.msg, '完成：', '回测未返回成功提示');
    must(post.scatter_points > 0, '图上没有买卖标记（散点为空）', post);
    must(post.table_rows > 0, '交易明细表为空', post);
    must(!post.empty_row, '交易明细显示「无数据」占位行', post);
    mustInclude(post.stats, '总收益', '绩效卡片未渲染总收益');
    must(!/NaN|undefined/.test(post.stats), '绩效卡片出现 NaN/undefined', post.stats);

    // ---- 交易明细：筛选 + 汇总 + 分页（默认第 1 页 / 每页 20 条 / 最近记录在前）----
    // 先切到交易较多的「止盈止损」，否则单笔的买入持有无法验证筛选与翻页
    await pickModel(s, '止盈止损');
    await s.waitUntil(`document.querySelectorAll('.trade-table tbody tr').length > 1`,
      { desc: '止盈止损模型的交易明细', timeout: 15000 });
    const tt = JSON.parse(await s.eval(`(() => {
      const txt = el => el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
      return JSON.stringify({
        rows: document.querySelectorAll('.trade-table tbody tr').length,
        bar: txt(document.querySelector('.tt-bar')),
        sum: txt(document.querySelector('.tt-sum')),
        pg: txt(document.querySelector('.tt-page')),
        selects: document.querySelectorAll('.tt-bar select').length,
        count: txt(document.querySelector('.tt-count')),
      });
    })()`));
    console.log('TRADE ' + JSON.stringify(tt));
    must(tt.selects >= 2 && !!tt.bar, '交易明细缺少筛选控件', tt);
    mustInclude(tt.sum, '累计净收益', '汇总卡未显示累计净收益');
    must(!/NaN|undefined/.test(tt.sum), '汇总卡出现 NaN/undefined', tt.sum);
    must(tt.rows > 0 && tt.rows <= 20, '默认每页应在 (0,20] 条', tt.rows);
    mustInclude(tt.pg, '第 1 /', '分页未停在第 1 页', tt.pg);
    mustInclude(tt.pg, '最近记录', '分页未标明第 1 页为最近记录', tt.pg);

    // 筛选「仅亏损」：行内净收益必须全部为负，且条数变化
    await s.eval(`(() => {
      const sel = document.querySelectorAll('.tt-bar select')[1];
      const set = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
      set.call(sel, 'loss'); sel.dispatchEvent(new Event('change', { bubbles: true }));
      return true; })()`);
    await sleep(350);
    const lossOnly = JSON.parse(await s.eval(`(() => {
      const rows = [...document.querySelectorAll('.trade-table tbody tr')];
      return JSON.stringify({
        rows: rows.length,
        nets: rows.map(r => (r.children[7] || {}).textContent || '').filter(Boolean),
        count: (document.querySelector('.tt-count') || {}).textContent || '',
      });
    })()`));
    console.log('FILTER loss ' + JSON.stringify(lossOnly));
    must(lossOnly.nets.length > 0, '筛选「仅亏损」后没有任何交易', lossOnly);
    must(lossOnly.nets.every(x => x.trim().startsWith('-')),
      '筛选「仅亏损」后仍出现盈利记录', lossOnly.nets.slice(0, 5));

    // 重置筛选：必须恢复与初始相同的条数
    await s.mustClick('重置筛选');
    await sleep(350);
    const back = await s.eval(`document.querySelectorAll('.trade-table tbody tr').length`);
    must(back === tt.rows, '重置筛选未恢复全部记录', `${back} vs ${tt.rows}`);

    // 翻页：有第 2 页时必须真的换页（首行内容变化 + 页码变成 2）
    const firstBefore = await s.eval(
      `((document.querySelector('.trade-table tbody tr') || {}).textContent || '')`);
    const pgAct = await s.eval(`(() => {
      const b = [...document.querySelectorAll('.tt-page button')].find(x => x.textContent.trim() === '下一页');
      if (!b || b.disabled) return 'single';
      b.click(); return 'ok'; })()`);
    if (pgAct === 'ok') {
      await sleep(350);
      const p2 = JSON.parse(await s.eval(`(() => JSON.stringify({
        first: (document.querySelector('.trade-table tbody tr') || {}).textContent || '',
        pg: (document.querySelector('.tt-page') || {}).textContent || '' }))()`));
      mustInclude(p2.pg, '第 2 /', '下一页未切到第 2 页', p2.pg);
      must(p2.first !== firstBefore, '翻页后首行内容未变化', p2.first.slice(0, 60));
      await s.eval(`(() => {
        const b = [...document.querySelectorAll('.tt-page button')].find(x => x.textContent.trim() === '首页');
        if (b && !b.disabled) b.click(); return true; })()`);
      await sleep(300);
    } else {
      console.log('分页：样本仅一页，跳过翻页断言');
    }

    console.log('shot: ' + await s.shot('ui_run_light.png', false));

    // 切到「模型对比」tab：必须真的换页并渲染对比表
    await s.mustClick('模型对比', { within: '.tabs' });
    await s.waitUntil(`document.querySelectorAll('.cmp-table tbody tr').length > 0`,
      { desc: '模型对比表渲染', timeout: 15000 });
    const cmp = await s.eval(`document.querySelectorAll('.cmp-table tbody tr').length`);
    must(cmp >= 3, '模型对比表行数少于勾选的模型数', cmp);
    console.log('shot compare: ' + await s.shot('ui_compare.png', false));

    // 暗色主题
    await s.mustClick('🌙');
    await s.waitUntil(`document.documentElement.dataset.theme === 'dark'`,
      { desc: '暗色主题生效', timeout: 10000 });
    console.log('theme=' + await s.eval(`document.documentElement.dataset.theme`));
    await sleep(600);
    console.log('shot dark: ' + await s.shot('ui_run_dark.png', false));

    s.assertClean();
  } finally {
    s.close();
  }
});
