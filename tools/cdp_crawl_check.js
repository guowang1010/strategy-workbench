// 验收：在线拉取失败时，失败原因必须显示在弹窗内（不再只有「请查看顶部提示」）
// 用法：node tools/cdp_crawl_check.js [url]
const { launch, run, must } = require('./cdp_lib');

const URL = process.argv[2] || 'http://127.0.0.1:8765/';
const BAD_CODE = '999999';   // 不存在的代码 → 整批失败 → 后端 502 带原因

run(async () => {
  const s = await launch({ port: 9366, url: URL });
  await s.waitUntil(`!!document.querySelectorAll('.topbar select')[1]?.selectedOptions[0]?.textContent`,
    { timeout: 30000, desc: '数据集和个股初始化完成' });

  // 0) 个股下拉必须显示日 K 范围（首日~末日 · N日）
  const opt = await s.eval(`document.querySelectorAll('.topbar select')[1]?.selectedOptions[0]?.textContent || ''`);
  must(/\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2} · \d+日/.test(opt), '个股下拉未显示日K范围', opt);
  console.log('个股下拉：' + opt.trim());

  // 0.5) 拉取弹窗默认勾选「合并到当前数据集」，并标明目标数据集
  await s.mustClick('拉取行情');
  must(await s.count('.crawl-form') === 1, '拉取弹窗未打开');
  const mergeChk = await s.eval(`(() => {
    const c = [...document.querySelectorAll('.crawl-form input[type=checkbox]')].pop();
    if (!c) return { missing: true };
    return { checked: c.checked, label: c.closest('label').textContent.trim() }; })()`);
  must(mergeChk.checked === true, '合并到当前数据集应默认勾选', mergeChk);
  must(/#\d+/.test(mergeChk.label || ''), '合并选项应标明目标数据集编号', mergeChk);
  console.log('合并选项：' + mergeChk.label);
  await s.eval(`(() => { [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '✕').click(); return 'ok'; })()`);

  // 1) 输入一个必失败的代码并开始拉取
  await s.mustClick('拉取行情');
  await s.eval(`(() => {
    const inp = document.querySelector('.crawl-codes input');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(inp, ${JSON.stringify(BAD_CODE)});
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    return 'ok'; })()`);
  await s.mustClick('开始拉取');

  // 2) 失败原因必须出现在弹窗内的红框里（.crawl-err），且带后端原始原因
  await s.waitUntil(`!!document.querySelector('.crawl-err')?.textContent.trim()`,
    { timeout: 120000, desc: '弹窗内失败提示' });
  const errText = await s.text('.crawl-err');
  must(errText.includes('拉取失败'), '弹窗内提示应以「拉取失败」开头', errText);
  must(errText.length > '拉取失败：'.length + 6, '失败提示缺少具体原因（不能只有标题）', errText);
  must(!errText.includes('请查看顶部提示'), '仍指向被遮罩挡住的顶部提示', errText);
  console.log('弹窗内失败原因：' + errText.trim());

  await s.shot('ui_crawl_err.png');
  // 本测试故意触发失败：/api/datasets/crawl 返回 502 是预期结果，豁免之
  await s.assertClean(['/api/datasets/crawl']);
});

