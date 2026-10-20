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
  document.addEventListener('DOMContentLoaded',function(){initMaps();initToday();initFold();tick();setInterval(tick,30000);});
})();
