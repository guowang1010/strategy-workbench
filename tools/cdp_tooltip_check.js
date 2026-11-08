const {launch,run,must}=require('./cdp_lib');
run(async()=>{
 const s=await launch({port:9390,url:process.argv[2]||'http://127.0.0.1:8768/'});
 await s.waitUntil(`!!window.__chart && !!window.__chart.getOption().series?.[0]?.data?.length`,{timeout:30000,desc:'K线初始化'});
 await s.eval(`(()=>{const c=window.__chart;const n=c.getOption().series[0].data.length;c.dispatchAction({type:'showTip',seriesIndex:0,dataIndex:n-5});})()`);
 const visible=`(()=>{const c=document.querySelector('.chart');return !!c && [...c.querySelectorAll('div')].some(d=>getComputedStyle(d).position==='absolute'&&d.offsetWidth>100&&d.innerText.includes('-')&&getComputedStyle(d).visibility!=='hidden');})()`;
 await s.waitUntil(visible,{timeout:10000,desc:'Tooltip显示'});
 console.log('SHOWTIP PASS');
 const pt=await s.eval(`(()=>{const c=window.__chart;const i=c.getOption().series[0].data.length-5;const p=c.convertToPixel({seriesIndex:0},[i,c.getOption().series[0].data[i][1]]);const r=document.querySelector('.chart').getBoundingClientRect();c.dispatchAction({type:'hideTip'});return {x:r.left+p[0],y:r.top+p[1]};})()`);
 await s.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:pt.x,y:pt.y,button:'none'});
 await s.waitUntil(visible,{timeout:10000,desc:'鼠标悬停Tooltip'});
 console.log('HOVER PASS');await s.shot('ui_tooltip_hover.png');await s.assertClean();s.close();
});
