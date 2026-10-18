(function(){
  // 地图：页面里每个 <div class="map" data-src="mapdata-xxx"> 读取同名 JSON
  function initMaps(){
    if(typeof L==='undefined')return;
    document.querySelectorAll('.map[data-src]').forEach(function(el){
      var d=JSON.parse(document.getElementById(el.dataset.src).textContent);
      var m=L.map(el,{scrollWheelZoom:false});
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'© OpenStreetMap'}).addTo(m);
      var pts=[];
      d.points.forEach(function(p){
        var c=p.ref?'#9a9a9a':(p.color||'#333');
        var mk=L.circleMarker([p.lat,p.lon],{radius:p.ref?5:8,color:c,fillColor:c,fillOpacity:p.ref?0.35:0.85,weight:2}).addTo(m);
        mk.bindPopup('<b>'+p.name+'</b>'+(p.days&&p.days.length?'<br>10/'+p.days.join('、10/'):'<br>参考（未入计划）')+(p.note?'<br>'+p.note:'')+(p.href?'<br><a href="'+p.href+'">地点页 →</a>':''));
        if(!p.ref)pts.push([p.lat,p.lon]);
      });
      (d.routes||[]).forEach(function(r){if(r.pts.length>1)L.polyline(r.pts,{color:r.color,weight:3,opacity:0.6,dashArray:'6 6'}).addTo(m);});
      if(pts.length)m.fitBounds(pts,{padding:[30,30],maxZoom:14});else m.setView([47.8,16.5],7);
      el.classList.add('ready');el.dataset.markers=d.points.length;
    });
  }
  // 出行当天：按今天日期显示当天卡片
  function initToday(){
    var box=document.getElementById('today');if(!box)return;
    var data=JSON.parse(document.getElementById('todaydata').textContent);
    var now=new Date(),key=('0'+now.getDate()).slice(-2),mon=now.getMonth()+1;
    var hit=(mon===10&&data.days[key])?key:null;
    var html='';
    if(hit){var d=data.days[hit];html='<h2>今天 '+d.title+'</h2>'+d.html;}
    else{
      var start=new Date('2026-10-25T00:00:00+02:00');var days=Math.ceil((start-now)/86400000);
      html='<p class="muted">今天不在行程日期内'+(days>0?'（距出发还有 '+days+' 天）':'')+'。下面是第一天的安排：</p>'+'<h2>'+data.days['25'].title+'</h2>'+data.days['25'].html;
    }
    box.innerHTML=html;
  }
  document.addEventListener('DOMContentLoaded',function(){initMaps();initToday();});
})();
