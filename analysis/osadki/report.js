
const TL = window.__TL__;
const ORDER = Object.keys(TL.days).sort();
const U = TL.units; const UKEYS = ["ryukzak_4600-4900","sklon_4900-5200","zona_5200-5500","ctrl_5099"];
const HL = {"4000":"4000 м — долина","4663":"4663 м — рюкзак","5018":"5018 м — лагерь 1","5435":"5435 м — зона","6000":"6000 м — гребень"};
let cur = (TL.today in TL.days) ? TL.today : ORDER[ORDER.length-1];
{ const m=(location.hash||"").match(/date=(\d{4}-\d{2}-\d{2})/); if(m&&TL.days[m[1]]) cur=m[1]; }  // глубокая ссылка #date=YYYY-MM-DD
let tab = "s2"; let sub = "zone_rgb";
function fmt(v,d=1,suf=""){ if(v===null||v===undefined) return "—"; return v.toFixed(d).replace(".",",")+suf; }
function tf(v){ if(v===null||v===undefined) return "—"; if(Math.abs(v)<0.05) v=0; const s=(v>0?"+":"")+v.toFixed(1); return s.replace(".",",").replace("+0,0","0,0")+" °C"; }
function dl(d){ return parseInt(d.slice(8,10))+"."+d.slice(5,7); }
function esc(s){ return String(s??"").replace(/</g,"&lt;"); }
function isClear(s){ return (s.coverage_zona||0)>=0.95; }
function buildStrip(){
  const el=document.getElementById("strip"); el.innerHTML="";
  const maxp = Math.max(5, ...ORDER.map(d=>(TL.days[d].model&&TL.days[d].model.prec)||0));
  for(const d of ORDER){ const D=TL.days[d]; const m=D.model||{};
    const div=document.createElement("div"); div.className="day"+(d===cur?" sel":"")+(m.forecast?" fc":"")+(d===TL.today?" today":""); div.dataset.date=d;
    const clear=D.s2.some(isClear); const cloudy=D.s2.length>0&&!clear;
    const trn=D.auto&&D.auto.trend?D.auto.trend:""; const tr=trn.startsWith("меньше")?"▼":(trn.startsWith("больше")?"▲":(trn?"•":""));
    const t46=m.t&&m.t["4663"]?m.t["4663"].tmax:null;
    div.innerHTML=`<div class="dn">${dl(d)}</div><div class="sub">${t46===null||t46===undefined?"":tf(t46).replace(" °C","°")}</div><div class="tr" title="снега стало: ${esc(trn)}">${tr}</div>
      <div class="bar"><i style="height:${Math.round(100*(m.prec||0)/maxp)}%" title="осадки по модели ${fmt(m.prec)} мм"></i></div>
      <div class="ic">${clear?'<b style="background:#5fd38a" title="ясный снимок Sentinel-2"></b>':(cloudy?'<b style="background:#555" title="Sentinel-2 в облаках"></b>':'')}${D.s1.length?'<b style="background:#b388ff" title="радар"></b>':''}${D.ai&&D.ai.ok?'<b style="background:#ffd200" title="есть оценка ИИ-агента"></b>':''}</div>`;
    div.onclick=()=>select(d); el.appendChild(div); }
}
function select(d){ cur=d; try{ history.replaceState(null,"","#date="+d); }catch(e){} document.querySelectorAll("#strip .day").forEach(x=>x.classList.toggle("sel",x.dataset.date===d)); const s=document.querySelector(`#strip .day[data-date="${d}"]`); if(s) s.scrollIntoView({inline:"center",block:"nearest",behavior:"smooth"}); render(); }
function step(k){ const i=ORDER.indexOf(cur)+k; if(i>=0&&i<ORDER.length) select(ORDER[i]); }
document.addEventListener("keydown",e=>{ if(e.key==="ArrowLeft") step(-1); if(e.key==="ArrowRight") step(1); });
function sp(spr, name){ if(!spr||!spr.p||!spr.p[name]) return null; const [x,y,w,h]=spr.p[name]; return {src:spr.src, x, y, w, h, W:spr.w, H:spr.h}; }
function spHtml(b){ // панель из спрайта: фон с масштабом, пропорции панели сохраняются
  const bs=(b.W/b.w*100).toFixed(3); const px=b.W>b.w?(b.x/(b.W-b.w)*100).toFixed(3):0; const py=b.H>b.h?(b.y/(b.H-b.h)*100).toFixed(3):0;
  return `<a href="${b.src}" target="_blank" title="открыть все панели"><div class="spv" style="aspect-ratio:${b.w}/${b.h};background-image:url('${b.src}');background-size:${bs}% auto;background-position:${px}% ${py}%"></div></a>`; }
function panelsFor(D){
  const out=[];
  if(tab==="s2"){ for(const s of D.s2){ const b=sp(s.sprite,sub); if(b) out.push({b,cap:`Sentinel-2 ${s.sat} ${s.time_utc} UTC · облачность тайла ${s.tile_cloud} % · площадка зоны чистая на ${Math.round((s.coverage_zona||0)*100)} %`}); } }
  if(tab==="mtn"){ for(const s of D.s2){ const b=sp(s.sprite,sub.replace("zone","mtn")); if(b) out.push({b,cap:`Вся гора 8×8 км, Sentinel-2 ${s.sat} ${s.time_utc} UTC`}); } }
  if(tab==="s1"){ for(const s of D.s1){ const d=sp(s.sprite,"zone_diff"); if(d) out.push({b:d,cap:`Радар ${s.orbit} ${s.time_utc} UTC: изменение к пролёту ${s.prev_date?dl(s.prev_date):"—"} (синее — сигнал упал: намокло/потеплело; красное — вырос; серое — без значимых изменений)`}); const v=sp(s.sprite,"zone_vv"); if(v) out.push({b:v,cap:`Радар ${s.orbit}: яркость VV (тёмное — мокрый снег или гладкая поверхность)`}); } }
  if(tab==="viirs"&&D.viirs){ const f=sp(D.viirs,"near_fc"); if(f) out.push({b:f,cap:"VIIRS 375 м, ложные цвета: голубое — снег/лёд, белое — облака, тёмное — камень. Кружок — зона интереса"}); const w=sp(D.viirs,"wide_tc"); if(w) out.push({b:w,cap:"VIIRS обычные цвета, 55×45 км — вся Алайская долина"}); }
  return out;
}
function render(){
  const D=TL.days[cur]; const m=D.model;
  document.getElementById("curdate").textContent=dl(cur)+(m&&m.forecast?" (прогноз)":"")+(cur===TL.today?" — сегодня":"");
  let h="";
  if(m){ const rows=["4000","4663","5018","5435","6000"].filter(k=>m.t[k]).map(k=>`<tr><td>${HL[k]}</td><td>${tf(m.t[k].tmax)}</td><td>${tf(m.t[k].tmin)}</td></tr>`).join("");
    const oth=Object.entries(m.prec_other||{});
    h+=`<div class="blk"><div class="lbl">🖥 Погодная модель ECMWF 9 км — ${m.forecast?"прогноз":"за прошедшие сутки (кратчайший прогноз, не измерение)"}</div>
    <table class="meta"><tr><th>Высота</th><th>днём</th><th>ночью</th></tr>${rows}</table>
    <p>Осадки: <b>${fmt(m.prec)} мм</b> воды за сутки${oth.length?` <span class="sub">(другие модели: ${oth.map(([k,v])=>k.split(" ")[0]+" "+fmt(v,0)).join(", ")})</span>`:""}${m.prec_hours&&m.prec_hours.length?`<br><span class="sub">часы с осадками (местное): ${m.prec_hours.map(x=>x+":00").join(", ")}</span>`:""}
    ${(m.snow_frac_4663!==null&&m.snow_frac_4663!==undefined)?`<br>На 4663 м снегом выпало ${Math.round(m.snow_frac_4663*100)} % осадков`:""}${(m.sun_h!==null&&m.sun_h!==undefined)?`<br>Солнце по модели: ${fmt(m.sun_h,1)} ч, облачность ${Math.round(m.cloud||0)} %`:""}
    ${m.ens?`<br>Ансамбль ECMWF (51 вариант): осадки ≥1 мм с вероятностью ${Math.round(m.ens.p_ge1*100)} %, ≥5 мм — ${Math.round(m.ens.p_ge5*100)} %`:""}</p>
    <p class="sub">Погрешность: осадки ±100 % и занижение коротких ливней/снегопадов; температура ±2 °C на 4663 м, на 5435 м знак не гарантирован (модели расходятся на 4–5 °C).</p></div>`; }
  const st=D.stations||{}; if(st.karakul||st.sarytash){ h+=`<div class="blk"><div class="lbl">📍 Метеостанции (реальные измерения, каждые 3 ч)</div>${st.karakul?`<p>Каракуль, 3930 м, 52 км южнее: ${tf(st.karakul.tmin)} … ${tf(st.karakul.tmax)}, осадки ${st.karakul.prec===null||st.karakul.prec===undefined?"нет данных":fmt(st.karakul.prec,1," мм")}${st.karakul.snow?", <b>был снег</b>":""}, облачность ${fmt(st.karakul.cloud_okta,0)}/8</p>`:""}${st.sarytash?`<p>Сары-Таш, 3150 м, 40 км северо-западнее: ${tf(st.sarytash.tmin)} … ${tf(st.sarytash.tmax)}${st.sarytash.snow?", <b>был снег</b>":""}, облачность ${fmt(st.sarytash.cloud_okta,0)}/8 <span class="sub">(осадки станция не передаёт)</span></p>`:""}</div>`; }
  if(D.s2.length){ h+=`<div class="blk"><div class="lbl">🛰 Индекс снега по Sentinel-2 (${D.s2.length} ${D.s2.length===1?"снимок":"снимка"})</div>`;
    for(const s of D.s2){ h+=`<table class="meta"><tr><th>${s.sat} ${s.time_utc} UTC, ${s.orbit}</th><th>закрыто снегом</th><th>камней (порог 0,3–0,5)</th><th>сев. склоны</th></tr>`;
      for(const k of UKEYS){ const u=s.idx[k]; const ok=(u.coverage||0)>=0.95; h+=`<tr><td style="color:${U[k].color}">${U[k].label}</td><td>${ok&&u.covered!==null?"<b>"+fmt(u.covered,0)+" %</b>":"<span class='sub'>—</span>"}</td><td>${ok&&u.rock40!==null?fmt(u.rock30,0)+"–"+fmt(u.rock50,0)+" % (0,4: "+fmt(u.rock40,0)+")":"<span class='sub'>облака, покрытие "+Math.round((u.coverage||0)*100)+" %</span>"}</td><td>${ok&&u.north_rock40!==null&&u.north_rock40!==undefined?fmt(u.north_rock40,0)+" %":"—"}</td></tr>`; }
      h+=`</table>`; }
    h+=`<p class="sub">«Закрыто снегом» — какая часть камней, открытых на самую бесснежную дату (20.08), сейчас под снегом: 0 % — как 20.08, 100 % — все закрыты. Число только если ≥95 % площадки без облаков. Погрешность одного значения ±3–5 п.п.; надёжны изменения больше ~8 п.п. на двух площадках сразу.</p></div>`; }
  if(D.auto&&D.auto.text) h+=`<div class="blk auto"><div class="lbl">⚙️ Алгоритмический расчёт по данным <span class="sub">(детерминированные правила, без генеративного текста)</span></div><p>${D.auto.text}</p></div>`;
  const ai=D.ai; if(ai){ if(ai.ok){ h+=`<div class="blk ai"><div class="lbl">✨ Оценка ИИ-агента <span class="aitag">сгенерированный текст · ${esc(ai.model)}</span> <span class="sub">${esc(ai.generated_at)}${ai.backfill?" · создана задним числом только по данным на эту дату":""} — модель прочитала те же данные и снимки и написала оценку; числа проверяйте по расчёту выше</span></div>
     <p><b>${esc(ai.summary)}</b></p><p>Снега стало: <b>${esc(ai.trend)}</b> — ${esc(ai.trend_basis)}</p><p><b>Что видно на снимках:</b> ${esc(ai.image_observations)}</p><p><b>Модель и факты:</b> ${esc(ai.model_vs_fact)}</p><p>Доверие: <b>${esc(ai.confidence)}</b> — ${esc(ai.confidence_why)}</p>${(ai.flags||[]).length?`<p>⚠ ${ai.flags.map(esc).join("<br>⚠ ")}</p>`:""}<p><b>За чем следить:</b> ${esc(ai.watch_next)}</p></div>`; }
    else h+=`<div class="blk ai"><div class="lbl">✨ Оценка ИИ-агента <span class="aitag">сгенерированный текст</span></div><p class="sub">за эту дату не создана: ${esc(ai.error||"ошибка")}</p></div>`; }
  else if(!(m&&m.forecast)) h+=`<div class="blk ai"><div class="lbl">✨ Оценка ИИ-агента <span class="aitag">сгенерированный текст</span></div><p class="sub">за эту дату оценка не создавалась (агент пишет её на день снимка Sentinel-2 и на текущий день)</p></div>`;
  document.getElementById("left").innerHTML=h;
  const tabs=[["s2","Спутник 3×3 км"],["mtn","Вся гора 8×8 км"],["s1","Радар"],["viirs","VIIRS (ежедневно)"]];
  let t=`<div class="tabs">${tabs.map(([k,l])=>`<button class="${tab===k?"on":""}" data-tab="${k}">${l}</button>`).join("")}</div>`;
  if(tab==="s2"||tab==="mtn") t+=`<div class="sub-tabs">${[["zone_rgb","обычные цвета"],["zone_mask","маска снег/камень"],["zone_swir","инфракрасный"]].map(([k,l])=>`<button class="${sub===k?"on":""}" data-sub="${k}">${l}</button>`).join("")}</div>`;
  const ps=panelsFor(D);
  const empty = (tab==="s2"||tab==="mtn")?"Пролёта Sentinel-2 в этот день не было (спутник проходит раз в 2–3 дня). Посмотрите вкладку VIIRS или соседние дни.":(tab==="s1"?"Пролёта радара в этот день не было (4 пролёта за 12 дней).":"Снимка VIIRS за этот день нет.");
  t+=`<div class="viewer">${ps.length?ps.map(p=>`<figure>${spHtml(p.b)}<figcaption>${p.cap}</figcaption></figure>`).join(""):`<p class="sub" style="padding:20px 6px">${empty}</p>`}</div>`;
  document.getElementById("right").innerHTML=t;
  document.querySelectorAll("[data-tab]").forEach(b=>b.onclick=()=>{tab=b.dataset.tab; render();});
  document.querySelectorAll("[data-sub]").forEach(b=>b.onclick=()=>{sub=b.dataset.sub; render();});
}
buildStrip(); render(); setTimeout(()=>{ const s=document.querySelector(`#strip .day[data-date="${cur}"]`); if(s) s.scrollIntoView({inline:"center",block:"nearest"}); },50);
document.querySelectorAll(".chart .pt").forEach(el=>el.addEventListener("click",()=>{ if(el.dataset.date&&TL.days[el.dataset.date]){ select(el.dataset.date); document.getElementById("brauzer").scrollIntoView({behavior:"smooth"}); } }));
document.getElementById("prev").onclick=()=>step(-1); document.getElementById("next").onclick=()=>step(1);
