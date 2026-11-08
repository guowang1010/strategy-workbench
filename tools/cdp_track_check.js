// CDP 验收：计划跟踪与复盘（BUY-12）
// 覆盖：选 M1 → 手动账户 → 研究模式 → 生成计划 → 保存并跟踪 → 录入实际成交 → 复盘结果
//
// 以前的坑：没等 init 完成、没确认模型/账户面板渲染，就去找 .p-acct / .pt-form 输入框，
// 拿到 no-inputs:0、addfill no、结果空，却仍 EXIT 0。
// 现在前置条件 waitReady + pickModel，每一步 must()，复盘结果必须有成交行与计划/实际对照。
const { launch, run, must, mustInclude, waitReady, pickModel, selectDataset } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';

run(async () => {
  const s = await launch({ url: URL, port: 9353, width: 1600, height: 1100, settle: 800 });
  try {
    await waitReady(s);
    await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });
    await s.mustClick('下一笔计划', { within: '.tabs' });
    await s.waitUntil(`!!document.querySelector('.plan-panel')`, { desc: '计划面板渲染', timeout: 15000 });

    // 买入持有：信号常开，便于演示「已确认 → 保存 → 成交 → 复盘」全链路
    console.log('model ' + await pickModel(s, '买入持有'));

    await s.mustClick('我的实际账户');
    await s.waitUntil(`document.querySelectorAll('.p-acct input').length >= 3`,
      { desc: '手动账户输入框', timeout: 10000 });
    const filled = await s.eval(`(() => {
      const set = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        d.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true }));
      };
      const ins = [...document.querySelectorAll('.p-acct input')];
      set(ins[0], '50000'); set(ins[1], '0'); set(ins[2], '0');
      return ins.slice(0, 3).map(x => x.value).join(',');
    })()`);
    console.log('fill ' + filled);
    must(filled === '50000,0,0', '手动账户输入未写入', filled);

    const research = await s.eval(`(() => {
      const cb = document.querySelector('.p-bar-right input[type=checkbox]');
      if (!cb) return 'no';
      if (!cb.checked) cb.click();
      return cb.checked ? 'on' : 'off';
    })()`);
    console.log('research ' + research);
    must(research === 'on', '研究模式未开启（陈旧行情会被判过期）', research);

    await s.mustClick('生成计划');
    await s.waitUntil(`(() => { const b = document.querySelector('.p-status .p-badge');
      return !!b && b.textContent.trim().length > 0; })()`,
      { desc: '计划状态徽章', timeout: 30000 });
    const plan = JSON.parse(await s.eval(`(() => {
      const txt = el => el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
      return JSON.stringify({
        status: txt(document.querySelector('.p-status .p-badge')),
        reasons: txt(document.querySelector('.p-status .p-reasons')),
        kvs: [...document.querySelectorAll('.p-kv')].map(txt),
        qty: txt(document.querySelector('.p-kv:nth-of-type(3)')),
      });
    })()`));
    console.log('PLAN ' + JSON.stringify(plan));
    must(plan.status.length > 0, '计划状态为空', plan);
    must(!/数据不足|无法判断/.test(plan.status), '计划被判数据不足，无法继续跟踪', plan);
    must(plan.kvs.length >= 6, '计划关键字段不足（数量/费用/保护价）', plan.kvs);
    must(plan.kvs.some(x => x.includes('保护价')), '计划缺少保护价', plan.kvs);
    must(plan.kvs.some(x => x.includes('预计数量')), '计划缺少预计数量', plan.kvs);

    // 保存计划并跟踪（未成交同 plan_id 会复用记录，两种提示都合法）
    await s.mustClick('保存计划并跟踪');
    await s.waitUntil(`/已保存到跟踪列表|已更新跟踪记录/.test((document.querySelector('.msg') || {}).textContent || '')`,
      { desc: '计划保存成功提示', timeout: 30000 });
    console.log('save ' + await s.text('.msg'));
    await s.waitUntil(`document.querySelectorAll('.pt-list .pt-row:not(.pt-hd)').length > 0`,
      { desc: '跟踪记录行', timeout: 15000 });
    const recs = await s.eval(`document.querySelectorAll('.pt-list .pt-row:not(.pt-hd)').length`);
    must(recs >= 1, '跟踪列表没有记录', recs);

    // 录入实际成交：表单必须出现且可提交（不是 no-inputs:0 / addfill no）
    await s.waitUntil(`document.querySelectorAll('.pt-form input').length >= 4`,
      { desc: '成交录入表单', timeout: 15000 });
    const form = await s.eval(`(() => {
      const set = (el, v) => {
        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        d.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true }));
      };
      const ins = [...document.querySelectorAll('.pt-form input')];
      set(ins[0], '5900'); set(ins[1], '8.31'); set(ins[2], '12.90');
      return ins.slice(0, 3).map(x => x.value).join(',') + '|date=' + (ins[3] ? ins[3].value : '');
    })()`);
    console.log('fillform ' + form);
    must(form.startsWith('5900,8.31,12.9'), '成交表单输入未写入', form);
    must(!form.endsWith('date='), '成交日为空，无法提交', form);
    await s.waitUntil(`(() => { const b = [...document.querySelectorAll('.pt-form button')]
      .find(x => x.textContent.includes('录入成交')); return !!b && !b.disabled; })()`,
      { desc: '录入成交按钮可用', timeout: 10000 });
    await s.mustClick('录入成交');
    await s.waitUntil(`/成交已录入/.test((document.querySelector('.msg') || {}).textContent || '')`,
      { desc: '成交录入成功提示', timeout: 20000 });

    // 复盘：必须有成交行 + 计划 vs 实际对照 + 结论
    await s.waitUntil(`document.querySelectorAll('.pt-frow:not(.pt-fhd)').length > 0`,
      { desc: '复盘成交行', timeout: 15000 });
    const t = JSON.parse(await s.eval(`(() => {
      const txt = el => el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
      return JSON.stringify({
        outcome: txt(document.querySelector('.pt-detail .p-status .p-badge')),
        kvs: [...document.querySelectorAll('.pt-detail .pt-kv')].map(txt),
        fills: [...document.querySelectorAll('.pt-frow:not(.pt-fhd)')].map(txt),
        warnings: [...document.querySelectorAll('.pt-warn div')].map(txt),
        timeline: [...document.querySelectorAll('.pt-tl li')].map(txt),
      });
    })()`));
    console.log('TRACK ' + JSON.stringify(t, null, 1));
    must(t.outcome.length > 0, '复盘结论为空', t);
    must(t.fills.length >= 1, '复盘没有成交记录', t);
    must(t.fills[0].includes('5,900'), '成交行数量不是录入的 5900', t.fills[0]);
    must(t.fills[0].includes('8.31'), '成交行价格不是录入的 8.31', t.fills[0]);
    const kv = t.kvs.join(' | ');
    mustInclude(kv, '计划数量', '复盘缺少「计划数量」对照');
    mustInclude(kv, '实际买入', '复盘缺少「实际买入」对照');
    must(t.timeline.length >= 2, '复盘时间线过短（应有计划/成交等节点）', t.timeline);

    console.log('shot: ' + await s.shot('ui_plan_track.png'));
    console.log('shot2: ' + await s.shot('ui_plan_review.png'));
    s.assertClean();
  } finally {
    s.close();
  }
});
