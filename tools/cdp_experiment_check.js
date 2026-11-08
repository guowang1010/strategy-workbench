const {launch,run,must}=require('./cdp_lib');
run(async()=>{
 const s=await launch({port:9361,url:process.argv[2]||'http://127.0.0.1:8768/'});
 await s.waitUntil(`!!document.querySelector('.model-card')`,{timeout:30000,desc:'初始化'});
 await s.mustClick('实验记录');
 await s.waitUntil(`!!document.querySelector('.exp-table tbody tr .exp-ops')`,{timeout:30000,desc:'实验列表'});
 await s.eval(`document.querySelector('.exp-table tbody tr').click()`);
 await s.waitUntil(`!!document.querySelector('.exp-detail .exp-json')`,{timeout:15000,desc:'实验详情'});
 console.log('DETAIL PASS');
 await s.eval(`[...document.querySelectorAll('.exp-tabs button')].find(x=>x.textContent.startsWith('寻优')).click()`);
 await s.waitUntil(`!!document.querySelector('.exp-table .exp-kind') && [...document.querySelectorAll('.exp-table .exp-kind')].every(x=>x.textContent.includes('寻优'))`,{timeout:15000,desc:'筛选寻优'});
 const before=await s.count('.exp-table tbody tr');
 await s.eval(`[...document.querySelectorAll('.exp-table tbody tr:first-child .exp-ops button')].find(x=>x.textContent.includes('重跑')).click()`);
 await s.waitUntil(`document.querySelectorAll('.exp-table tbody tr').length>${before}`,{timeout:180000,desc:'重跑产生新实验'});
 console.log('FILTER REPLAY PASS');await s.shot('ui_exp_replay.png');await s.assertClean();s.close();
});
