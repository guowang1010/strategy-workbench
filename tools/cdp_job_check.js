// CDP 验收：寻优任务进度条 + 取消
// 覆盖：选 M4（有默认搜索空间）→ 提交大网格 → 进度可读且推进 → 取消按钮 → 终态「已取消」
//
// 以前：没选模型就点「参数寻优」，后端回「该模型无默认搜索空间」；任务条不存在、
// 取消按钮不存在，脚本却照样 EXIT 0。现在每一步 must()，取消必须有终态证据。
const { launch, run, must, mustInclude, waitReady, pickModel, selectDataset, sleep } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';

run(async () => {
  const s = await launch({ url: URL, port: 9357, width: 1600, height: 1000, settle: 800 });
  try {
    await waitReady(s);
    await selectDataset(s, { label: '默认样本', code: '600186', minBars: 200 });
    // M1/M2 没有默认搜索空间；必须显式选中并展示 M4（18000 组，足够观察进度与取消）
    console.log('model ' + await pickModel(s, '止盈止损'));

    await s.mustClick('参数寻优', { within: '.tabs' });
    // 任务一提交，store 立刻把 msg 改成「开始计算… 2%」，所以不能只等「已排队」文案；
    // 以「任务条出现」为准，失败则要求 msg 明确说失败原因。
    await s.waitUntil(`(() => {
      if (document.querySelector('.job-bar')) return true;
      const m = (document.querySelector('.msg') || {}).textContent || '';
      return /寻优失败|无默认搜索空间|请先选择/.test(m);
    })()`, { desc: '寻优任务启动', timeout: 20000 });
    const msg1 = await s.text('.msg');
    const hasBar = await s.eval(`!!document.querySelector('.job-bar')`);
    console.log('submit ' + msg1 + ' bar=' + hasBar);
    must(hasBar, '寻优任务没有启动（任务条缺失）', msg1);
    must(!/寻优失败|无默认搜索空间/.test(msg1), '寻优提交失败', msg1);
    const read = () => s.eval(`(() => {
      const b = document.querySelector('.job-bar');
      if (!b) return JSON.stringify({ gone: true });
      const t = (b.querySelector('.job-text') || {}).textContent || '';
      const m = t.match(/(\\d+)%/);
      return JSON.stringify({
        text: t.replace(/\\s+/g, ' ').trim(),
        pct: m ? Number(m[1]) : -1,
        fillW: Math.round((b.querySelector('.job-fill') || { getBoundingClientRect: () => ({ width: 0 }) })
          .getBoundingClientRect().width),
        cancel: !![...b.querySelectorAll('button')].find(x => x.textContent.includes('取')),
      });
    })()`);
    const j1 = JSON.parse(await read());
    console.log('running ' + JSON.stringify(j1));
    must(!j1.gone, '任务条在读取时消失（任务结束太快或未启动）', j1);
    must(j1.text.length > 0, '任务条没有进度文案', j1);
    must(j1.cancel, '任务条没有取消按钮', j1);
    await sleep(1200);
    const j2 = JSON.parse(await read());
    console.log('running2 ' + JSON.stringify(j2));
    must(!j2.gone, '任务在取消前已结束（无法验证取消路径）', j2);
    must(j2.pct >= 0, '进度条读数不是百分比', j2);
    must(j2.pct >= j1.pct, '进度倒退', { j1, j2 });

    console.log('shot running: ' + await s.shot('ui_job_running.png', false));

    await s.mustRealClick('.job-bar button');
    await s.waitUntil(`/寻优已取消/.test((document.querySelector('.msg') || {}).textContent || '')`,
      { desc: '取消终态（寻优已取消）', timeout: 60000 });
    await s.waitUntil(`!document.querySelector('.job-bar')`,
      { desc: '任务条随取消消失', timeout: 30000 });
    const msg2 = await s.text('.msg');
    console.log('final ' + msg2);
    mustInclude(msg2, '寻优已取消', '取消后没有得到终态提示');
    console.log('shot cancelled: ' + await s.shot('ui_job_cancelled.png', false));

    s.assertClean();
  } finally {
    s.close();
  }
});
