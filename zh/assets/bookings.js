// 买票与预约、要联系的人、优惠页的小脚本：按种类筛选、复制草稿、标截止、锚点在收起的块里时展开。
(function(){
  function viennaDate(d){return new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Vienna',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);}
  // 截止：卡片上 data-due 是 MM-DD（2026 年）；还没办的才提示
  function initDue(){
    var today=viennaDate(new Date());
    document.querySelectorAll('.duenote[data-due]').forEach(function(n){
      var card=n.closest('[data-due]');var box=n.closest('.bk');
      var open=box&&(box.getAttribute('data-open')==='1'||box.classList.contains('st-open'));
      var d='2026-'+n.dataset.due;
      if(!open)return;
      if(d<today){n.textContent=' 已过截止';n.className='duenote bkdue late';}
      else if(d===today){n.textContent=' 今天截止';n.className='duenote bkdue soon';}
    });
  }
  function initChips(){
    var bar=document.getElementById('bkchips');if(!bar)return;
    var cards=[].slice.call(document.querySelectorAll('details.bk'));
    function apply(k){
      cards.forEach(function(c){
        var show=!k||(k==='__open'?c.getAttribute('data-open')==='1':c.dataset.kind===k);
        c.hidden=!show;
      });
      document.querySelectorAll('.bkgroup').forEach(function(g){
        var any=[].some.call(g.querySelectorAll('details.bk'),function(c){return !c.hidden;});
        g.hidden=!any;
      });
      [].forEach.call(bar.querySelectorAll('.chip'),function(b){b.classList.toggle('on',b.dataset.k===(k||''));});
    }
    bar.addEventListener('click',function(e){var b=e.target.closest('.chip');if(b)apply(b.dataset.k);});
  }
  function copyText(t,done){
    function fallback(){var ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}document.body.removeChild(ta);done();}
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,fallback);}else{fallback();}
  }
  function initCopy(){
    document.addEventListener('click',function(e){
      var b=e.target.closest('.cpbtn[data-copy]');if(!b)return;
      var pre=document.getElementById(b.dataset.copy);if(!pre)return;
      copyText(pre.textContent,function(){var old=b.textContent;b.textContent='已复制 ✓';b.classList.add('ok');setTimeout(function(){b.textContent=old;b.classList.remove('ok');},1800);});
    });
  }
  // 链接指到收起的 details 里（如优惠页末尾的“不划算”）：展开它并滚过去
  function openHash(){
    var id=decodeURIComponent((location.hash||'').slice(1));if(!id)return;
    var el=document.getElementById(id);if(!el)return;
    for(var p=el;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true;}
    if(el.tagName==='DETAILS'&&el.classList.contains('bk'))el.open=true;
    el.scrollIntoView();
  }
  initDue();initChips();initCopy();openHash();
  window.addEventListener('hashchange',openHash);
})();
