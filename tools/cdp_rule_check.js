// CDP 验收：自定义规则编辑器（M9/M10）
// 覆盖：勾选 M9 → 编辑器渲染 → 载入模板「放量突破前高」→ 预览文本更新 → 回测请求带 rule → 出交易明细
//
// 以前失败点：没等 init 完成就找 M9 卡片（no-m9-card），然后在空对象上取属性抛异常 EXIT 1。
// 现在先 waitReady + pickModel，每一步 must()，业务结果（请求体 / 交易行）都要断言。
const { launch, run, must, mustInclude, waitReady, pickModel, selectDataset } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';

run(async () => {
  const s = await launch({ url: URL, port: 9410, width: 1600, height: 1200, settle: 800 });
  try {
    await waitReady(s);
    await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });

    // 1) 勾选并展示 M9（按 .mname 精确匹配，避免命中 M10 或 M4）
    const active = await pickModel(s, '自定义规则(止盈止损)');
    console.log('1) 展示模型:', active);

    // 2) 编辑器渲染：容器、下拉、条件行、预览文本都必须在
    await s.waitUntil(`!!document.querySelector('.rule-editor')`,
      { desc: '规则编辑器渲染', timeout: 15000 });
    const ed = JSON.parse(await s.eval(`(() => {
      const ed = document.querySelector('.rule-editor');
      const txt = el => el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
      return JSON.stringify({
        selects: ed.querySelectorAll('select').length,
        conds: ed.querySelectorAll('.rule-cond').length,
        groups: ed.querySelectorAll('.rule-group').length,
        preview: txt(ed.querySelector('.rule-preview')),
        templateOptions: ed.querySelectorAll('.rule-templates option').length,
      });
    })()`));
    console.log('2) 编辑器:', JSON.stringify(ed));
    must(ed.selects >= 2, '规则编辑器下拉不足（条件无法编辑）', ed);
    must(ed.conds >= 1, '规则编辑器没有条件行', ed);
    must(ed.groups >= 1, '规则编辑器没有条件组', ed);
    must(ed.preview.length > 0, '规则预览文本为空', ed);
    const previewBefore = ed.preview;

    // 3) 载入模板：预览文本必须随之改变，且仍是可读的规则描述
    const tpl = JSON.parse(await s.eval(`(async () => {
      const ed = document.querySelector('.rule-editor');
      const sel = ed.querySelector('.rule-templates select');
      if (!sel) return JSON.stringify({ error: 'no-template-select' });
      const opt = [...sel.options].find(o => o.textContent.includes('放量突破前高'));
      if (!opt) return JSON.stringify({ error: 'no-template-option' });
      sel.value = opt.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      await new Promise(r => setTimeout(r, 600));
      const ed2 = document.querySelector('.rule-editor');
      const txt = el => el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
      return JSON.stringify({
        conds: ed2.querySelectorAll('.rule-cond').length,
        preview: txt(ed2.querySelector('.rule-preview')),
      });
    })()`));
    console.log('3) 载入模板:', JSON.stringify(tpl));
    must(!tpl.error, '模板下拉不可用', tpl);
    must(tpl.conds >= 1, '模板载入后条件行丢失', tpl);
    must(tpl.preview.length > 0, '模板载入后预览为空', tpl);
    must(tpl.preview !== previewBefore, '载入模板后预览文本没有更新（模板未生效）', { previewBefore, tpl });

    // 4) 跑回测：请求体必须带 rule（证明编辑器内容真的进了引擎），并产生交易明细
    await s.mustClick('运行回测');
    await s.waitUntil(`/完成：|失败/.test((document.querySelector('.msg') || {}).textContent || '')`,
      { desc: '规则回测完成', timeout: 90000 });
    const msg = await s.text('.msg');
    console.log('4) 回测:', msg);
    mustInclude(msg, '完成：', '规则回测未成功');

    const runReq = [...s.requests].reverse().find(r => /\/api\/runs/.test(r.url));
    must(runReq, '没有捕获到 /api/runs 请求', s.requests.map(r => r.url).slice(-5));
    mustInclude(runReq.postData, '"rule"', '回测请求体没有 rule 字段（编辑器内容未提交）');
    mustInclude(runReq.postData, 'M9', '回测请求体没有 M9 模型');

    await s.waitUntil(`document.querySelectorAll('.trade-table tbody tr').length > 0`,
      { desc: '规则回测交易明细', timeout: 15000 });
    const rows = JSON.parse(await s.eval(`(() => {
      const rs = [...document.querySelectorAll('.trade-table tbody tr')]
        .filter(r => r.querySelector('td:not(.empty)'));
      return JSON.stringify({ total: rs.length, first: rs[0] ? rs[0].textContent.replace(/\\s+/g, ' ').trim() : '' });
    })()`));
    console.log('5) 交易明细:', JSON.stringify(rows));
    must(rows.total > 0, '规则回测没有产生交易明细', rows);
    must(!/无数据|—$/.test(rows.first), '交易明细首行是空占位', rows);

    s.assertClean();
  } finally {
    s.close();
  }
});
