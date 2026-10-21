(function(){
  // 地图：<div class="map" data-src="x"> 读同页 <script type="application/json" id="x">{points:[{lat,lon,name,note,href,color}]}</script>
  function initMaps(){
    if(typeof L==='undefined')return;
    document.querySelectorAll('.map[data-src]').forEach(function(el){
      var d=JSON.parse(document.getElementById(el.dataset.src).textContent);
      var m=L.map(el,{scrollWheelZoom:false});
      // OSM 瓦片政策要求不带子域名的地址与署名
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(m);
      var pts=[];
      d.points.forEach(function(p){
        var c=p.color||'#333';
        L.circleMarker([p.lat,p.lon],{radius:8,color:c,fillColor:c,fillOpacity:0.85,weight:2}).addTo(m)
          .bindPopup('<b>'+p.name+'</b>'+(p.note?'<br>'+p.note:'')+(p.href?'<br><a href="'+p.href+'">看卡片 →</a>':'')
            +'<br><a href="https://www.google.com/maps/dir/?api=1&destination='+p.lat+','+p.lon+'&travelmode=transit">公交导航</a>');
        pts.push([p.lat,p.lon]);
      });
      if(pts.length)m.fitBounds(pts,{padding:[30,30],maxZoom:14});else m.setView([47.8,16.5],7);
    });
  }
  // 首页：按维也纳日期显示今天与明天；行程没开始显示第一天，结束后显示最后一天
  function viennaDate(d){return new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Vienna',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);}
  function initToday(){
    var box=document.getElementById('nowcards');if(!box)return;
    var cards=[].slice.call(box.querySelectorAll('.daycard[data-date]'));if(!cards.length)return;
    var dates=cards.map(function(c){return c.dataset.date;});
    var now=new Date(),today=viennaDate(now),tomorrow=viennaDate(new Date(now.getTime()+86400000));
    var show=[],label={},note='';
    if(today<dates[0]){
      show=[dates[0]];label[dates[0]]='第一天';
      var days=Math.round((Date.parse(dates[0]+'T00:00:00Z')-Date.parse(today+'T00:00:00Z'))/86400000);
      note='行程还没开始，还有 '+days+' 天出发。';
    }else if(today>dates[dates.length-1]){
      show=[dates[dates.length-1]];label[show[0]]='最后一天';note='行程已结束。';
    }else{
      if(dates.indexOf(today)>=0){show.push(today);label[today]='今天';}
      if(dates.indexOf(tomorrow)>=0){show.push(tomorrow);label[tomorrow]='明天';}
    }
    cards.forEach(function(c){
      var on=show.indexOf(c.dataset.date)>=0;c.hidden=!on;
      var t=c.querySelector('.label');if(on&&t)t.textContent=label[c.dataset.date];
    });
    var n=document.getElementById('nownote');if(n)n.textContent=note;
  }
  // 硬节点倒计时：[data-at] 是带时区的 ISO 时刻
  function tick(){
    var now=Date.now();
    document.querySelectorAll('[data-at]').forEach(function(el){
      var cd=el.querySelector('.cd');if(!cd)return;
      var ms=Date.parse(el.dataset.at)-now,m=Math.round(ms/60000);
      if(ms<0){cd.textContent='已过';cd.classList.add('past');return;}
      cd.classList.remove('past');
      cd.textContent=m<60?('还有 '+m+' 分'):m<48*60?('还有 '+Math.floor(m/60)+' 小时 '+(m%60)+' 分'):('还有 '+Math.floor(m/1440)+' 天');
    });
  }
  function initFold(){
    document.querySelectorAll('[data-fold]').forEach(function(b){b.addEventListener('click',function(){
      var o=b.dataset.fold==='open';document.querySelectorAll('details.fold').forEach(function(d){d.open=o;});
    });});
  }
  // “选地方”页：勾选与想哪天存在 localStorage（读写失败也照常能用，只是不记住）；底部按钮把选择变成一段纯文字
  var PICK_KEY='trip2026-pick-v1';
  function loadPick(){try{var v=JSON.parse(localStorage.getItem(PICK_KEY)||'{}');return v&&typeof v==='object'&&!Array.isArray(v)?v:{};}catch(e){return {};}}
  function savePick(sel){try{localStorage.setItem(PICK_KEY,JSON.stringify(sel));}catch(e){}}
  function initPick(){
    var root=document.getElementById('pick');if(!root)return;
    var sel=loadPick(),kinds=[];
    var cards=[].slice.call(root.querySelectorAll('.pcard[data-id]'));
    function render(card){
      var id=card.dataset.id,on=Object.prototype.hasOwnProperty.call(sel,id),days=on&&Array.isArray(sel[id])?sel[id]:[];
      card.classList.toggle('sel',on);
      card.querySelector('.pk').checked=on;
      card.querySelector('.pwhen').hidden=!on;
      [].forEach.call(card.querySelectorAll('.dchip'),function(b){
        var d=b.dataset.d,act=d?days.indexOf(d)>=0:!days.length;
        b.classList.toggle('on',act);b.setAttribute('aria-pressed',act?'true':'false');
      });
    }
    function count(){
      var n=cards.filter(function(c){return Object.prototype.hasOwnProperty.call(sel,c.dataset.id);}).length;
      document.getElementById('pkcount').textContent='已选 '+n+' 个';return n;
    }
    function setOn(card,on){
      var id=card.dataset.id;
      if(on){if(!Object.prototype.hasOwnProperty.call(sel,id))sel[id]=[];}else{delete sel[id];}
      render(card);
    }
    function changed(){savePick(sel);count();}
    cards.forEach(function(card){
      render(card);
      card.querySelector('.pk').addEventListener('change',function(e){setOn(card,e.target.checked);changed();});
      [].forEach.call(card.querySelectorAll('.dchip'),function(b){b.addEventListener('click',function(){
        var id=card.dataset.id,d=b.dataset.d,days=Array.isArray(sel[id])?sel[id].slice():[];
        if(!d){days=[];}else{var i=days.indexOf(d);if(i>=0)days.splice(i,1);else days.push(d);}
        var order=[].map.call(card.querySelectorAll('.dchip[data-d]'),function(x){return x.dataset.d;});
        days.sort(function(a,b){return order.indexOf(a)-order.indexOf(b);});
        sel[id]=days;render(card);changed();
      });});
    });
    function visible(card){return !card.hidden&&!card.closest('.pgroup').hidden;}
    [].forEach.call(root.querySelectorAll('[data-all]'),function(b){b.addEventListener('click',function(){
      var on=b.dataset.all==='on';cards.forEach(function(c){if(!on||visible(c))setOn(c,on);});changed();
    });});
    [].forEach.call(root.querySelectorAll('.pgroup'),function(g){
      [].forEach.call(g.querySelectorAll('[data-group]'),function(b){b.addEventListener('click',function(){
        var on=b.dataset.group==='on';
        [].forEach.call(g.querySelectorAll('.pcard[data-id]'),function(c){if(!on||!c.hidden)setOn(c,on);});changed();
      });});
    });
    // 类别筛选：可多选；“全部”清掉筛选。筛掉的卡只是看不见，勾选不变
    var chips=[].slice.call(root.querySelectorAll('.chip[data-kind]'));
    function applyFilter(){
      chips.forEach(function(c){var k=c.dataset.kind,act=k?kinds.indexOf(k)>=0:!kinds.length;
        c.classList.toggle('on',act);c.setAttribute('aria-pressed',act?'true':'false');});
      var shown=0;
      cards.forEach(function(c){c.hidden=kinds.length>0&&kinds.indexOf(c.dataset.kind)<0;if(!c.hidden)shown++;});
      [].forEach.call(root.querySelectorAll('.pgroup'),function(g){g.hidden=!g.querySelector('.pcard:not([hidden])');});
      var el=document.getElementById('pkshown');if(el)el.textContent=kinds.length?('显示 '+shown+' / '+cards.length+' 个'):'';
    }
    chips.forEach(function(c){c.addEventListener('click',function(){
      var k=c.dataset.kind;
      if(!k){kinds=[];}else{var i=kinds.indexOf(k);if(i>=0)kinds.splice(i,1);else kinds.push(k);}
      applyFilter();
    });});
    applyFilter();count();
    function pickText(){
      var lines=['我在“选地方”页选了这些（网站生成于 '+(root.dataset.generated||'')+'）：'];
      cards.forEach(function(c){
        var id=c.dataset.id;if(!Object.prototype.hasOwnProperty.call(sel,id))return;
        var days=Array.isArray(sel[id])&&sel[id].length?sel[id].join('、'):'不限';
        lines.push('- ['+id+'] '+c.dataset.name+'（想哪天：'+days+'）');
      });
      lines.push('没选的我先不去。');
      return lines.join('\n');
    }
    var toast=document.getElementById('pktoast'),toastT=null;
    function say(msg){toast.textContent=msg;toast.hidden=false;clearTimeout(toastT);toastT=setTimeout(function(){toast.hidden=true;},2600);}
    var dlg=document.getElementById('pkdlg'),ta=document.getElementById('pktext');
    function showText(t){ta.value=t;dlg.hidden=false;ta.focus();ta.select();}
    document.getElementById('pkselect').addEventListener('click',function(){ta.focus();ta.select();try{ta.setSelectionRange(0,ta.value.length);}catch(e){}});
    ta.addEventListener('focus',function(){ta.select();});
    document.getElementById('pkclose').addEventListener('click',function(){dlg.hidden=true;});
    document.getElementById('pkcopy').addEventListener('click',function(){
      var n=count();if(!n){say('还没选地方：先在卡片上点“想去”');return;}
      var t=pickText();
      try{
        if(!navigator.clipboard||!window.isSecureContext)throw new Error('no clipboard');
        navigator.clipboard.writeText(t).then(function(){say('已复制 '+n+' 个地方，去贴给 agent');},function(){showText(t);});
      }catch(e){showText(t);}
    });
    window.tripPickText=pickText;  // 便于在浏览器控制台里查看将要复制的文字
  }
  document.addEventListener('DOMContentLoaded',function(){initMaps();initToday();initFold();initPick();tick();setInterval(tick,30000);});
})();
