(function(){
  function esc(t){return String(t==null?'':t).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  // 地图：<div class="map" data-src="x"> 读同页 <script type="application/json" id="x">{points:[{lat,lon,name,note,href,color}],
  // near:[{lat,lon,name,kind,km,walk,from,href}]}</script>。near 是库里离当天地点 5 km 内的其他地点，画成灰点
  // （≤2.5 km 深灰，之外浅灰）；页面上 <input data-near-for="x"> 是开关。
  function initMaps(){
    if(typeof L==='undefined')return;
    document.querySelectorAll('.map[data-src]').forEach(function(el){
      var d=JSON.parse(document.getElementById(el.dataset.src).textContent);
      var m=L.map(el,{scrollWheelZoom:false});
      // OSM 瓦片政策要求不带子域名的地址与署名
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(m);
      var pts=d.points.map(function(p){return [p.lat,p.lon];});
      if(pts.length)m.fitBounds(pts,{padding:[30,30],maxZoom:14});else m.setView([47.8,16.5],7);  // 先定视图，再加图层
      var nearLayer=L.layerGroup();
      (d.near||[]).forEach(function(p){
        var c=p.walk?'#6f6f76':'#bdbdc2';
        L.circleMarker([p.lat,p.lon],{radius:5,color:'#fff',weight:1,fillColor:c,fillOpacity:0.9}).addTo(nearLayer)
          .bindPopup('<b>'+esc(p.name)+'</b>'+(p.kind?' <span class="muted">'+esc(p.kind)+'</span>':'')
            +'<br>离 '+esc(p.from)+' 约 '+esc(p.km)+' km（'+(p.walk?'步行可达':'要坐车')+'）'
            +(p.href?'<br><a href="'+esc(p.href)+'">看卡片 →</a>':'')
            +'<br><a href="https://www.google.com/maps/dir/?api=1&destination='+p.lat+','+p.lon+'&travelmode=transit">公交导航</a>');
      });
      var sw=document.querySelector('input[data-near-for="'+el.dataset.src+'"]');
      function applyNear(){if(!sw||sw.checked)nearLayer.addTo(m);else m.removeLayer(nearLayer);}
      applyNear();  // 先加灰点，再加彩色点，彩色的压在上面
      if(sw)sw.addEventListener('change',applyNear);
      d.points.forEach(function(p){
        var c=p.color||'#333';
        L.circleMarker([p.lat,p.lon],{radius:8,color:c,fillColor:c,fillOpacity:0.85,weight:2}).addTo(m)
          .bindPopup('<b>'+p.name+'</b>'+(p.note?'<br>'+p.note:'')+(p.href?'<br><a href="'+p.href+'">看卡片 →</a>':'')
            +'<br><a href="https://www.google.com/maps/dir/?api=1&destination='+p.lat+','+p.lon+'&travelmode=transit">公交导航</a>');
      });
      el._map=m;
    });
  }
  // 图片放大：页内浮层，不改网址、不留历史。点图、点空白、按 Esc 或点 × 都关；图有 more 时 ‹ › 或键盘左右键翻，手机上左右滑。
  // 链接 <a data-gal='[{"src","cap"}]'> 没有脚本时照旧直接打开图片文件。
  var lb=null;
  function lightbox(){
    if(lb)return lb;
    var root=document.createElement('div');root.className='lb';root.hidden=true;root.setAttribute('role','dialog');root.setAttribute('aria-modal','true');root.setAttribute('aria-label','图片放大');
    root.innerHTML='<button type="button" class="lbx" aria-label="关闭">×</button><button type="button" class="lbp" aria-label="上一张">‹</button><button type="button" class="lbn" aria-label="下一张">›</button><img alt=""><div class="cap"></div>';
    document.body.appendChild(root);
    lb={root:root,img:root.querySelector('img'),cap:root.querySelector('.cap'),x:root.querySelector('.lbx'),p:root.querySelector('.lbp'),n:root.querySelector('.lbn'),list:[],i:0,from:null};
    function stop(e){e.stopPropagation();}
    lb.p.addEventListener('click',function(e){stop(e);lbShow(lb.i-1);});
    lb.n.addEventListener('click',function(e){stop(e);lbShow(lb.i+1);});
    lb.x.addEventListener('click',function(e){stop(e);lbClose();});
    root.addEventListener('click',lbClose);  // 点图、点空白、点说明都关
    var sx=null;
    root.addEventListener('touchstart',function(e){sx=e.touches.length===1?e.touches[0].clientX:null;},{passive:true});
    root.addEventListener('touchend',function(e){
      if(sx===null||lb.list.length<2)return;
      var dx=e.changedTouches[0].clientX-sx;sx=null;
      if(Math.abs(dx)>50){lbShow(lb.i+(dx<0?1:-1));lb.swiped=Date.now();}
    },{passive:true});
    root.addEventListener('click',function(e){if(lb.swiped&&Date.now()-lb.swiped<400){e.stopImmediatePropagation();}},true);
    return lb;
  }
  function lbShow(i){
    var n=lb.list.length;if(!n)return;
    lb.i=(i+n)%n;var it=lb.list[lb.i];
    lb.img.src=it.src;lb.img.alt=it.cap||'';
    lb.cap.innerHTML=esc(it.cap||'')+(n>1?'<span class="n">'+(lb.i+1)+' / '+n+'</span>':'');
    lb.p.hidden=lb.n.hidden=n<2;
  }
  function lbOpen(list,i,from){
    lightbox();lb.list=list;lb.from=from||null;lb.root.hidden=false;
    document.body.classList.add('lbopen');lbShow(i||0);lb.x.focus();
  }
  function lbClose(){
    if(!lb||lb.root.hidden)return;
    lb.root.hidden=true;lb.img.removeAttribute('src');document.body.classList.remove('lbopen');
    if(lb.from&&lb.from.focus){try{lb.from.focus({preventScroll:true});}catch(e){}}
  }
  function initLightbox(){
    document.addEventListener('click',function(e){
      var a=e.target.closest&&e.target.closest('a[data-gal]');
      if(!a||e.defaultPrevented||e.button>0||e.metaKey||e.ctrlKey||e.shiftKey)return;
      var list;try{list=JSON.parse(a.dataset.gal);}catch(x){return;}
      if(!list||!list.length)return;
      e.preventDefault();lbOpen(list,0,a);
    });
  }
  // 时间轴：点一个块，把它隐藏的详情放进弹窗（点 ×、点空白、按 Esc 或点详情里的链接都关）
  function initTimeline(){
    var modal=document.getElementById('tlmodal');if(!modal)return;
    var body=document.getElementById('tlmodalbody'),from=null;
    function close(){if(modal.hidden)return;modal.hidden=true;document.body.classList.remove('lbopen');if(from&&from.focus){try{from.focus({preventScroll:true});}catch(e){}}}
    document.querySelectorAll('.blk[data-d]').forEach(function(b){b.addEventListener('click',function(){
      var src=document.getElementById(b.dataset.d);if(!src)return;
      body.innerHTML=src.innerHTML;from=b;modal.hidden=false;document.body.classList.add('lbopen');
      modal.querySelector('.x').focus();
    });});
    modal.addEventListener('click',function(e){if(e.target===modal||e.target.closest('.x')||e.target.closest('a'))close();});
    modal._close=close;
  }
  document.addEventListener('keydown',function(e){
    if(lb&&!lb.root.hidden){
      if(e.key==='Escape'){lbClose();e.preventDefault();}
      else if(e.key==='ArrowLeft'&&lb.list.length>1){lbShow(lb.i-1);e.preventDefault();}
      else if(e.key==='ArrowRight'&&lb.list.length>1){lbShow(lb.i+1);e.preventDefault();}
      return;
    }
    var m=document.getElementById('tlmodal');
    if(e.key==='Escape'&&m&&!m.hidden&&m._close){m._close();e.preventDefault();}
  });
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
  // “选地方”页：每个地方三态——想去（可选想哪天）、不去、都不选＝中间（可去可不去）。存在 localStorage（读写失败也照常能用，
  // 只是不记住）；底部按钮把选择变成一段纯文字。存法 v2：{want:{id:[想哪天]}, no:[id]}；没有 v2 时把旧版 v1（{id:[想哪天]}，
  // 只有勾选）读成“想去”，并存成 v2。
  var PICK_KEY='trip2026-pick-v2',PICK_OLD='trip2026-pick-v1';
  function has(o,k){return Object.prototype.hasOwnProperty.call(o,k);}
  function isObj(v){return !!v&&typeof v==='object'&&!Array.isArray(v);}
  function readKey(k){try{var s=localStorage.getItem(k);return s?JSON.parse(s):null;}catch(e){return null;}}
  function strs(x){return Array.isArray(x)?x.filter(function(d){return typeof d==='string';}):[];}
  function savePick(st){try{localStorage.setItem(PICK_KEY,JSON.stringify({want:st.want,no:Object.keys(st.no)}));}catch(e){}}
  function loadPick(){
    var st={want:{},no:{}},v=readKey(PICK_KEY);
    if(isObj(v)){
      if(isObj(v.want))Object.keys(v.want).forEach(function(id){st.want[id]=strs(v.want[id]);});
      strs(v.no).forEach(function(id){if(!has(st.want,id))st.no[id]=true;});
      return st;
    }
    var old=readKey(PICK_OLD);
    if(isObj(old)){Object.keys(old).forEach(function(id){st.want[id]=strs(old[id]);});savePick(st);}
    return st;
  }
  function initPick(){
    var root=document.getElementById('pick');if(!root)return;
    var st=loadPick(),kinds=[];
    var cards=[].slice.call(root.querySelectorAll('.pcard[data-id]'));
    function state(id){return has(st.want,id)?'want':has(st.no,id)?'no':'';}
    function dateOrder(card){return [].map.call(card.querySelectorAll('.dchip[data-d]'),function(x){return x.dataset.d;}).filter(Boolean);}
    function render(card){
      var id=card.dataset.id,s=state(id),days=s==='want'?st.want[id]:[];
      card.classList.toggle('sel',s==='want');card.classList.toggle('no',s==='no');
      [].forEach.call(card.querySelectorAll('.cbtn'),function(b){b.setAttribute('aria-pressed',b.dataset.c===s?'true':'false');});
      card.querySelector('.pwhen').hidden=s!=='want';
      [].forEach.call(card.querySelectorAll('.dchip'),function(b){
        var d=b.dataset.d,act=d?days.indexOf(d)>=0:!days.length;
        b.classList.toggle('on',act);b.setAttribute('aria-pressed',act?'true':'false');
      });
    }
    function setState(card,s){
      var id=card.dataset.id;
      if(s==='want'){if(!has(st.want,id))st.want[id]=[];delete st.no[id];}
      else if(s==='no'){delete st.want[id];st.no[id]=true;}
      else{delete st.want[id];delete st.no[id];}
      render(card);
    }
    function count(){
      var c={want:0,no:0};
      cards.forEach(function(x){var s=state(x.dataset.id);if(s)c[s]++;});
      c.mid=cards.length-c.want-c.no;
      var t='想去 '+c.want+'｜不去 '+c.no+'｜中间 '+c.mid;
      ['pkstat','pkcount'].forEach(function(i){var el=document.getElementById(i);if(el)el.textContent=t;});
      return c;
    }
    function changed(){savePick(st);count();}
    cards.forEach(function(card){
      var id=card.dataset.id,order=dateOrder(card);
      if(has(st.want,id))st.want[id]=st.want[id].filter(function(d){return order.indexOf(d)>=0;});  // 去掉页面上已没有的日期
      render(card);
      [].forEach.call(card.querySelectorAll('.cbtn'),function(b){b.addEventListener('click',function(){
        setState(card,state(id)===b.dataset.c?'':b.dataset.c);changed();
      });});
      [].forEach.call(card.querySelectorAll('.dchip'),function(b){b.addEventListener('click',function(){
        if(state(id)!=='want')return;
        var d=b.dataset.d,days=st.want[id].slice();
        if(!d){days=[];}else{var i=days.indexOf(d);if(i>=0)days.splice(i,1);else days.push(d);}
        days.sort(function(a,b){return order.indexOf(a)-order.indexOf(b);});
        st.want[id]=days;render(card);changed();
      });});
    });
    var toast=document.getElementById('pktoast'),toastT=null;
    function say(msg){toast.textContent=msg;toast.hidden=false;clearTimeout(toastT);toastT=setTimeout(function(){toast.hidden=true;},2600);}
    // “都想去”：只改看得到的（按类别筛选时），已标“不去”的不改、已想去的保留想哪天；“清空”：想去与不去都清掉
    function allWant(list){
      var skip=0,done=0;
      list.forEach(function(c){if(c.hidden)return;var s=state(c.dataset.id);if(s==='no'){skip++;return;}if(!s){setState(c,'want');done++;}});
      changed();
      if(skip)say('设了 '+done+' 个想去；已标“不去”的 '+skip+' 个没改');
    }
    function visible(card){return !card.hidden&&!card.closest('.pgroup').hidden;}
    [].forEach.call(root.querySelectorAll('[data-all]'),function(b){b.addEventListener('click',function(){
      if(b.dataset.all==='on')allWant(cards.filter(visible));
      else{cards.forEach(function(c){setState(c,'');});changed();}
    });});
    [].forEach.call(root.querySelectorAll('.pgroup'),function(g){
      var gc=[].slice.call(g.querySelectorAll('.pcard[data-id]'));
      [].forEach.call(g.querySelectorAll('[data-group]'),function(b){b.addEventListener('click',function(){
        if(b.dataset.group==='on')allWant(gc);
        else{gc.forEach(function(c){setState(c,'');});changed();}
      });});
    });
    // 类别筛选：可多选；“全部”清掉筛选。筛掉的卡只是看不见，选择不变
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
    // 复制的文字只用 id 与中文名；中间的只给个数
    function pickText(){
      var want=[],no=[],mid=0;
      cards.forEach(function(c){
        var id=c.dataset.id,s=state(id);
        if(s==='want')want.push('- ['+id+'] '+c.dataset.name+'（想哪天：'+(st.want[id].length?st.want[id].join('、'):'不限')+'）');
        else if(s==='no')no.push('- ['+id+'] '+c.dataset.name);
        else mid++;
      });
      return ['我在“选地方”页选了这些（网站生成于 '+(root.dataset.generated||'')+'）：','想去：'].concat(want.length?want:['（没有）'],
        ['不去：'],no.length?no:['（没有）'],['中间（可去可不去）：'+(mid?'其余 '+mid+' 个，不一一列出。':'没有。')]).join('\n');
    }
    var dlg=document.getElementById('pkdlg'),ta=document.getElementById('pktext');
    function showText(t){ta.value=t;dlg.hidden=false;ta.focus();ta.select();}
    document.getElementById('pkselect').addEventListener('click',function(){ta.focus();ta.select();try{ta.setSelectionRange(0,ta.value.length);}catch(e){}});
    ta.addEventListener('focus',function(){ta.select();});
    document.getElementById('pkclose').addEventListener('click',function(){dlg.hidden=true;});
    document.getElementById('pkcopy').addEventListener('click',function(){
      var c=count();if(!c.want&&!c.no){say('还没选：先在卡片上点“想去”或“不去”');return;}
      var t=pickText();
      try{
        if(!navigator.clipboard||!window.isSecureContext)throw new Error('no clipboard');
        navigator.clipboard.writeText(t).then(function(){say('已复制：想去 '+c.want+' 个、不去 '+c.no+' 个，去贴给 agent');},function(){showText(t);});
      }catch(e){showText(t);}
    });
    window.tripPickText=pickText;  // 便于在浏览器控制台里查看将要复制的文字
  }
  document.addEventListener('DOMContentLoaded',function(){initMaps();initLightbox();initTimeline();initToday();initFold();initPick();tick();setInterval(tick,30000);});
})();
