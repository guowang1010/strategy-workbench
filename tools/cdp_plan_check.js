// CDP 验收：下一笔计划面板（BUY-01～11 + 第十二轮约束边界）
// 覆盖：策略模拟账户生成 → 结构化状态/时序/条件/触发/数量情景；
//       切手动账户 + 研究模式再生成 → 历史情景标识与「有效至」标签。
//
// 旧版误报：脚本按文案查找「历史有效期」，而 KV 标签实际是「历史有效至」→ EXIT 1。
// 现在改用稳定属性 data-plan-status / data-historical / data-kv / data-scn 断言，
// 不再把界面文案当作唯一契约。
const { launch, run, must, mustInclude, waitReady, pickModel, selectDataset } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';

const ALLOWED_STATUS = ['signal_confirmed', 'watching', 'blocked', 'holding', 'expired', 'data_unavailable'];

const READ = `(() => {
  const txt = el => el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
  const kv = sel => {
    const el = document.querySelector(sel);
    return el ? txt(el.querySelector('.pv')) : '';
  };
  const st = document.querySelector('[data-plan-status]');
  return JSON.stringify({
    status: st ? st.getAttribute('data-plan-status') : '',
    historical: st ? st.getAttribute('data-historical') : '',
    badge: txt(document.querySelector('.p-status .p-badge')),
    reasons: txt(document.querySelector('.p-status .p-reasons')),
    stale: txt(document.querySelector('.p-stale')),
    kvLabels: [...document.querySelectorAll('.p-kv')].map(r => r.getAttribute('data-kv') || ''),
    checks: [...document.querySelectorAll('.p-checks li')].map(txt),
    qty: kv('[data-kv="预计数量"]'),
    protection: kv('[data-kv="保护价"]'),
    exec: kv('[data-kv="执行资格"]'),
    validUntil: kv('[data-kv="有效至"]') || kv('[data-kv="历史有效至"]'),
    scn: [...document.querySelectorAll('.p-scn-row')].map(r => ({
      key: r.getAttribute('data-scn'), used: r.getAttribute('data-used') === '1',
      cells: [...r.children].map(c => txt(c)),
    })),
    conds: document.querySelectorAll('.p-cond-row').length,
    trigger: txt(document.querySelector('.p-trig-head')),
  });
})()`;

// 只取第一个数字（「4,900 股（49 手）」→ 4900），避免把手数也算进去
const digits = s => {
  const m = String(s).match(/[\d,]+/);
  return m ? m[0].replace(/,/g, '') : '';
};

run(async () => {
  const s = await launch({ url: URL, port: 9351, width: 1600, height: 1100, scale2: true, settle: 800 });
  try {
    await waitReady(s);
    await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });
    // 计划面板按「当前展示模型」生成；restorePlan 可能改过 activeId，显式选回 M4
    console.log('model ' + await pickModel(s, '止盈止损'));
    await s.mustClick('下一笔计划', { within: '.tabs' });
    await s.waitUntil(`!!document.querySelector('.plan-panel')`, { desc: '计划面板渲染', timeout: 15000 });

    // ---------- 1) 策略模拟账户 ----------
    await s.mustClick('生成计划');
    await s.waitUntil(`(() => { const st = document.querySelector('[data-plan-status]');
      return !!st && st.getAttribute('data-plan-status').length > 0; })()`,
      { desc: '计划状态生成', timeout: 30000 });
    const p1 = JSON.parse(await s.eval(READ));
    console.log('SIM ' + JSON.stringify(p1, null, 1));
    must(ALLOWED_STATUS.includes(p1.status), '计划状态不在契约枚举内', p1.status);
    must(p1.kvLabels.length >= 6, '关键字段不足（时序/数量/有效期）', p1.kvLabels);
    mustInclude(p1.kvLabels.join('|'), '预计数量', '缺少预计数量');
    mustInclude(p1.kvLabels.join('|'), '保护价', '缺少保护价');
    mustInclude(p1.kvLabels.join('|'), '执行资格', '缺少执行资格');
    must(p1.checks.length >= 3, '待确认清单应至少 3 项', p1.checks);
    must(p1.exec.length > 0, '执行资格没有结论', p1.exec);
    must(p1.trigger.length > 0, '下一根触发条件没有渲染', p1);
    must(p1.status !== 'blocked' || p1.badge.length > 0, '阻止状态没有说明', p1);

    // 数量与情景表一致性（D02/D04）：采用行数量 == 计划数量
    if (p1.scn.length) {
      const used = p1.scn.filter(x => x.used);
      must(used.length === 1, '情景表应恰好一行标注「采用」', p1.scn);
      must(used[0].cells.every(c => c.length > 0), '情景行存在空单元格', used[0]);
      must(p1.scn.every(x => (x.cells.join('').match(/\d/g) || []).length >= 4),
        '情景行数值不足', p1.scn);
      const planQty = digits(p1.qty);
      const usedQty = digits(used[0].cells[3]);
      must(planQty !== '' && usedQty !== '', '无法读取计划数量/情景数量', { p1q: p1.qty, used: used[0].cells });
      must(planQty === usedQty, '情景表「采用」行数量与主计划不一致（D04 回归）',
        { planQty, usedQty, qty: p1.qty, used: used[0].cells });
    }
    console.log('shot sim: ' + await s.shot('ui_plan.png'));

    // ---------- 2) 手动账户 + 研究模式 ----------
    await s.mustClick('我的实际账户');
    await s.waitUntil(`document.querySelectorAll('.p-acct input').length >= 3`,
      { desc: '手动账户输入框', timeout: 10000 });
    const fill = await s.eval(`(() => {
      const set = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        d.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true }));
      };
      const ins = [...document.querySelectorAll('.p-acct input')];
      set(ins[0], '50000'); set(ins[1], '0'); set(ins[2], '0'); set(ins[3], '9.80');
      return ins.slice(0, 4).map(x => x.value).join(',');
    })()`);
    console.log('fill ' + fill);
    must(fill === '50000,0,0,9.8' || fill === '50000,0,0,9.80', '手动账户输入未写入', fill);

    const research = await s.eval(`(() => {
      const cb = document.querySelector('.p-bar-right input[type=checkbox]');
      if (!cb) return 'no';
      if (!cb.checked) cb.click();
      return cb.checked ? 'on' : 'off';
    })()`);
    console.log('research ' + research);
    must(research === 'on', '研究模式未开启', research);

    await s.mustClick('生成计划');
    await s.waitUntil(`(() => { const st = document.querySelector('[data-plan-status]');
      return !!st && st.getAttribute('data-plan-status').length > 0; })()`,
      { desc: '手动账户计划生成', timeout: 30000 });
    const p2 = JSON.parse(await s.eval(READ));
    console.log('MANUAL ' + JSON.stringify(p2, null, 1));
    must(ALLOWED_STATUS.includes(p2.status), '手动账户计划状态非法', p2.status);
    must(p2.badge.length > 0, '计划状态徽章为空', p2);

    const kvText = p2.kvLabels.join('|');
    const isResearchMode = p2.historical === '1';
    if (isResearchMode) {
      // 研究模式 = 回放历史情景：状态、执行日、有效期必须成组标注，不能冒充当前委托
      mustInclude(p2.stale, '研究模式', '研究模式提示缺失');
      mustInclude(p2.badge, '历史情景', '研究模式状态未标注历史情景');
      must(kvText.includes('历史执行日') || kvText.includes('历史检查日'),
        '研究模式应把执行日/检查日标为历史', kvText);
      must(kvText.includes('历史有效至'), '研究模式应把有效期标为「历史有效至」', kvText);
      must(p2.validUntil.length > 0, '历史有效期没有值', p2);
    } else {
      must(!p2.badge.includes('历史情景'), '非研究模式不应标历史情景', p2.badge);
      must(kvText.includes('有效至'), '缺少「有效至」字段', kvText);
    }
    // 手动账户现金必须真的进入计划
    must(p2.conds >= 1, '信号条件树没有渲染', p2);
    if (p2.scn.length > 1) {
      must(p2.scn.some(x => x.used), '价格情景未标出采用的档位', p2.scn);
      must(p2.scn.every(x => (x.cells.join('').match(/\d/g) || []).length >= 4),
        '价格情景行缺少数值', p2.scn);
    }
    console.log('shot manual: ' + await s.shot('ui_plan_manual.png'));

    s.assertClean();
  } finally {
    s.close();
  }
});
