#!/usr/bin/env python3
"""Сборка отчёта об активности волонтёров TG-группы.

Входы: analysis/tg_activity.json (агрегаты tg_activity.py),
       analysis/tg_participants.json (scripts/tg_participants.py),
       analysis/tg_scores.json (LLM-оценки обсуждений по дайджестам).
Выход: analysis/viewer/otchet-tg-aktivnost.html (сортируемые таблицы).

Запуск: python3 analysis/build_tg_report.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "analysis"
OUT = A / "viewer" / "otchet-tg-aktivnost.html"

agg = json.loads((A / "tg_activity.json").read_text(encoding="utf-8"))
participants = json.loads((A / "tg_participants.json").read_text(encoding="utf-8"))
scores = {s["author"]: s for s in
          json.loads((A / "tg_scores.json").read_text(encoding="utf-8"))}

for a in agg:
    s = scores.get(a["author"])
    a["discussion"] = s["discussion"] if s else None
    a["usefulness"] = s["usefulness"] if s else None
    a["comment"] = s["comment"] if s else ""

silent = [m for m in participants if m["messages"] == 0]

n_group_msgs = sum(a["messages"] for a in agg)
period = f"{min(a['first_day'] for a in agg)} — {max(a['last_day'] for a in agg)}"

html = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Курумды — активность волонтёров в TG-группе</title>
<style>
  :root {
    --bg:#101216; --panel:#181b21; --panel2:#1e222a; --text:#d7dbe0;
    --muted:#8b93a0; --accent:#7ab8ff; --line:#2a2f38;
    --ok:#5fd38a; --mid:#ffd200; --warn:#ff6b6b;
  }
  * { box-sizing:border-box; }
  html,body { margin:0; background:var(--bg); color:var(--text);
    font:15px/1.55 -apple-system,"Segoe UI",Roboto,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:#101216ee;
    border-bottom:1px solid var(--line); padding:10px 16px; backdrop-filter:blur(6px); }
  header nav { display:flex; gap:16px; flex-wrap:wrap; align-items:baseline;
    max-width:1200px; margin:0 auto; }
  header .title { font-weight:700; }
  header a { color:var(--accent); text-decoration:none; }
  main { max-width:1200px; margin:0 auto; padding:20px 16px 80px; }
  h1 { font-size:26px; line-height:1.25; margin:18px 0 6px; }
  h2 { font-size:21px; margin:44px 0 10px; padding-top:14px;
    border-top:2px solid var(--line); }
  p { margin:8px 0; }
  .sub { color:var(--muted); }
  .note { border-left:3px solid var(--accent); background:var(--panel2);
    padding:10px 14px; border-radius:0 8px 8px 0; margin:14px 0; font-size:13.5px; }
  .note ul { margin:4px 0; padding-left:18px; }
  .kpis { display:flex; gap:12px; flex-wrap:wrap; margin:16px 0; }
  .kpi { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:10px 16px; }
  .kpi b { display:block; font-size:22px; }
  .kpi span { color:var(--muted); font-size:12.5px; }
  .tblwrap { overflow-x:auto; border:1px solid var(--line); border-radius:10px; }
  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th, td { padding:6px 9px; border-top:1px solid var(--line);
    text-align:left; vertical-align:top; }
  thead th { border-top:0; background:var(--panel); color:var(--muted);
    font-weight:600; font-size:12.5px; cursor:pointer; user-select:none;
    white-space:nowrap; position:sticky; top:0; }
  thead th:hover { color:var(--text); }
  thead th .arr { color:var(--accent); }
  tbody tr:nth-child(odd) { background:#14171c; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  td.name { white-space:nowrap; font-weight:600; }
  td.comment { color:var(--muted); min-width:260px; }
  .bar { display:inline-block; height:9px; border-radius:3px;
    background:var(--accent); opacity:.55; vertical-align:middle; margin-right:6px; }
  .s { display:inline-block; min-width:22px; text-align:center; border-radius:5px;
    padding:0 4px; font-weight:700; }
  .s0 { background:#3a2226; color:var(--warn); }
  .s1 { background:#3a3322; color:var(--mid); }
  .s2 { background:#1f3a2a; color:var(--ok); }
  .m { color:var(--muted); }
</style>
</head>
<body>
<header><nav>
  <span class="title">Активность волонтёров TG</span>
  <a href="map.html">Карта</a>
  <a href="index.html">Кандидаты</a>
  <a href="otchet-2026-08-17-koshki-pokrytie.html">Отчёт 17.08</a>
</nav></header>
<main>

<h1>Активность волонтёров в TG-группе «Анализ видео с дронам»</h1>
<p class="sub">Период __PERIOD__ · сформировано 2026-08-17 по полной выгрузке групповых тем
(scripts/tg_export.py) и списку участников (scripts/tg_participants.py). Личные диалоги не учитываются.</p>

<div class="kpis">
  <div class="kpi"><b>__N_PART__</b><span>участников в группе</span></div>
  <div class="kpi"><b>__N_AUTH__</b><span>писали сообщения</span></div>
  <div class="kpi"><b>__N_SILENT__</b><span>молчуны (0 сообщений)</span></div>
  <div class="kpi"><b>__N_MSGS__</b><span>сообщений в темах группы</span></div>
  <div class="kpi"><b>__N_ACC__</b><span>принятых кадров (кластеров)</span></div>
</div>

<div class="note">
<b>Методика</b> (все метрики — оценки по данным чата):
<ul>
<li><b>Часы</b> — суммарная длительность «сессий» присутствия в чате: сообщения с разрывом
&le;45 мин считаются одной сессией, +10 мин за факт каждой сессии. Это время активности
в чате, реальное время отсмотра видео оно недооценивает (человек может часами смотреть молча).</li>
<li><b>Кандидаты</b> — сообщения с фото в темах подачи находок (Новые скриншоты, Вещи,
Под вопросом, Гипотезы, Для перепроверки дроном).</li>
<li><b>Принято</b> — кадры, попавшие в тему «Подтвержденные»: картинки сматчены перцептивным
хэшем (dHash), кластер засчитан самому раннему подателю кандидата; если кадр в «Подтвержденных»
не находит поданного кандидата, он засчитывается запостившему (модераторам это может
приписывать чужие находки, поданные вне выгруженных тем).</li>
<li><b>Обсуждения / Полезность</b> (0–10) — эвристическая LLM-оценка по дайджесту всех
сообщений автора (только авторы с &ge;15 сообщениями): вовлечённость в диалог и
аргументированность/конкретика соответственно. Это субъективная оценка, не рейтинг людей.</li>
</ul>
</div>

<h2>1. Авторы — активность и вклад</h2>
<p class="sub">Клик по заголовку столбца — сортировка. По умолчанию — по числу сообщений.</p>
<div class="tblwrap">
<table id="t1">
<thead><tr>
<th data-k="author" data-t="s">Автор</th>
<th data-k="messages" data-t="n">Сообщ.</th>
<th data-k="days_active" data-t="n">Дней</th>
<th data-k="est_hours" data-t="n">Часы (оценка)</th>
<th data-k="candidates" data-t="n">Кандидаты (фото)</th>
<th data-k="accepted" data-t="n">Принято</th>
<th data-k="discussion" data-t="n">Обсуждения</th>
<th data-k="usefulness" data-t="n">Полезность</th>
<th data-k="comment" data-t="s">Комментарий (LLM)</th>
</tr></thead>
<tbody></tbody>
</table>
</div>

<h2>2. Молчуны — вступили, но не написали ни одного сообщения</h2>
<p class="sub">__N_SILENT__ из __N_PART__ участников. Сопоставление по user id, поэтому
совпадения имён с активными авторами — это разные аккаунты. Часть может отсматривать
видео молча — «молчун» не значит «не участвует».</p>
<div class="tblwrap">
<table id="t2">
<thead><tr>
<th data-k="name" data-t="s">Имя</th>
<th data-k="username" data-t="s">Username</th>
<th data-k="joined" data-t="s">Вступил(а)</th>
</tr></thead>
<tbody></tbody>
</table>
</div>

</main>
<script>
const AUTHORS = __AUTHORS__;
const SILENT = __SILENT__;

const maxMsg = Math.max(...AUTHORS.map(a => a.messages));
function scoreCell(v) {
  if (v === null || v === undefined) return '<span class="m">—</span>';
  const cls = v >= 7 ? 's2' : v >= 4 ? 's1' : 's0';
  return `<span class="s ${cls}">${v}</span>`;
}
function esc(s) { return String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function renderT1(rows) {
  document.querySelector('#t1 tbody').innerHTML = rows.map(a => `<tr>
    <td class="name">${esc(a.author)}</td>
    <td class="num"><span class="bar" style="width:${Math.round(40*a.messages/maxMsg)}px"></span>${a.messages}</td>
    <td class="num">${a.days_active}</td>
    <td class="num">${a.est_hours}</td>
    <td class="num">${a.candidates || '<span class="m">·</span>'}</td>
    <td class="num">${a.accepted || '<span class="m">·</span>'}</td>
    <td class="num">${scoreCell(a.discussion)}</td>
    <td class="num">${scoreCell(a.usefulness)}</td>
    <td class="comment">${esc(a.comment)}</td>
  </tr>`).join('');
}
function renderT2(rows) {
  document.querySelector('#t2 tbody').innerHTML = rows.map(m => `<tr>
    <td class="name">${esc(m.name)}</td>
    <td>${m.username ? '@' + esc(m.username) : '<span class="m">—</span>'}</td>
    <td class="num">${esc(m.joined || '—')}</td>
  </tr>`).join('');
}

function sortable(tableId, rows, render, defaultKey) {
  let key = defaultKey, dir = -1;
  const ths = document.querySelectorAll(`#${tableId} thead th`);
  function apply() {
    const t = [...rows].sort((a, b) => {
      let x = a[key], y = b[key];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (typeof x === 'string') return dir * x.localeCompare(y, 'ru');
      return dir * (x - y);
    });
    ths.forEach(th => {
      th.innerHTML = th.innerHTML.replace(/ <span class="arr">.*<\\/span>/, '');
      if (th.dataset.k === key)
        th.innerHTML += ` <span class="arr">${dir < 0 ? '▼' : '▲'}</span>`;
    });
    render(t);
  }
  ths.forEach(th => th.addEventListener('click', () => {
    if (th.dataset.k === key) dir = -dir;
    else { key = th.dataset.k; dir = th.dataset.t === 's' ? 1 : -1; }
    apply();
  }));
  apply();
}
sortable('t1', AUTHORS, renderT1, 'messages');
sortable('t2', SILENT, renderT2, 'joined');
</script>
</body>
</html>
"""

html = (html
        .replace("__PERIOD__", period)
        .replace("__N_PART__", str(len(participants)))
        .replace("__N_AUTH__", str(len(agg)))
        .replace("__N_SILENT__", str(len(silent)))
        .replace("__N_MSGS__", str(n_group_msgs))
        .replace("__N_ACC__", str(sum(a["accepted"] for a in agg)))
        .replace("__AUTHORS__", json.dumps(
            [{k: a[k] for k in ("author", "messages", "days_active", "est_hours",
                                "candidates", "accepted", "discussion",
                                "usefulness", "comment")} for a in agg],
            ensure_ascii=False))
        .replace("__SILENT__", json.dumps(
            [{k: m[k] for k in ("name", "username", "joined")} for m in silent],
            ensure_ascii=False)))

OUT.write_text(html, encoding="utf-8")
print(f"ok: {OUT} ({OUT.stat().st_size // 1024} КБ)")
