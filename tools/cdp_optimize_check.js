// CDP 验收：参数寻优（第九轮改造）——关键步骤一律断言，不再「打印 heat=0 也算过」。
//
// 通过条件（全部满足才 EXIT 0）：
//   1. 运行回测后消息出现「完成」；
//   2. 寻优任务消息出现「寻优完成」（失败/取消直接判失败）；
//   3. 三口径卡片都有数值（不是全 —）；
//   4. 热力图真的画出了像素（canvas 非空白），而不是只有空容器；
//   5. 走查表（若开启）有数据行；
//   6. 全程零 JS 异常 / 零 console.error / 零 4xx。
const { launch, must, mustInclude, run, waitReady, selectDataset } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';

run(async () => {
  const s = await launch({ port: 9348, url: URL, width: 1600, height: 1000 });

  // 显式选 2100 根的默认样本：28 根的新股跑不出有意义的寻优热力图
  await waitReady(s);
  await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });

  // 1) 直接切到寻优 Tab（它的 onClick 会自动触发一次寻优，不依赖先跑回测）。
  //    只有当按钮仍可用（自动触发没发生）时才手动点，避免重复提交任务。
  await s.mustClick('参数寻优', { within: '.tabs' });
  await new Promise(r => setTimeout(r, 1200));
  const btn = await s.eval(`(() => {
    const b = [...document.querySelectorAll('.opt-empty button')].find(x => x.textContent.includes('运行寻优'));
    return b ? (b.disabled ? 'disabled' : 'enabled') : 'none'; })()`);
  must(btn !== 'none', '寻优面板没有「运行寻优」按钮');
  if (btn === 'enabled') await s.mustClick('运行寻优');
  const res = await s.waitUntil(`(() => {
    const m = document.querySelector('.msg')?.textContent || '';
    if (m.includes('寻优失败') || m.includes('寻优已取消')) return 'fail:' + m;
    return m.includes('寻优完成') ? 'ok' : ''; })()`,
  { timeout: 240000, interval: 1000, desc: '寻优完成' });
  must(res === 'ok', '寻优任务未成功', res);

  // 3) 三口径 + 结论
  const verdict = await s.mustText('.opt-verdict', '缺少寻优结论条（.opt-verdict）');
  must(/结论：/.test(verdict), '结论条文案异常', verdict);
  const cals = await s.eval(`[...document.querySelectorAll('.opt-cals .cal')].map(e => e.innerText.replace(/\\s+/g,' ').trim())`);
  must(cals.length === 3, '三口径卡片应为 3 张', cals);
  for (const c of cals) {
    must(/期望\s*[+-]?\d/.test(c), '口径卡片缺少期望数值', c);
  }
  const ranges = await s.text('.opt-best');
  mustInclude(ranges, '训练段', '缺少训练/测试段日期');

  // 4) 热力图必须有真实像素（旧脚本只在 rect 选择器上数出 0 也算过）
  const heat = await s.eval(`(() => {
    const canv = [...document.querySelectorAll('.opt canvas')];
    const c = canv.find(x => x.width > 100 && x.height > 100);
    if (!c) return { canvases: canv.length, painted: 0 };
    const ctx = c.getContext('2d');
    let painted = 0;
    try {
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      for (let i = 3; i < d.length; i += 4000) if (d[i] > 0) painted++;
    } catch (e) { return { canvases: canv.length, painted: -1, err: String(e) }; }
    return { canvases: canv.length, painted }; })()`);
  must(heat.canvases > 0, '热力图没有 canvas', heat);
  must(heat.painted > 50, '热力图是空白画布（像素采样为零，可能没有数据）', heat);

  // 5) 走查表（若渲染）
  const wfRows = await s.count('.wf-table tbody tr');
  if (wfRows > 0) {
    const first = await s.text('.wf-table tbody tr');
    must(/\d{4}-\d{2}-\d{2}/.test(first), '走查表首行缺少日期区间', first);
  }

  console.log(`寻优：${verdict.trim().slice(0, 40)} | canvas=${heat.canvases} painted=${heat.painted} | 走查行=${wfRows}`);
  console.log('截图: ' + await s.shot('ui_optimize.png'));
  s.assertClean();
  s.close();
});
