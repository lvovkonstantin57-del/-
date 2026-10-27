"""Страницы без входа: расписание группы прямо в HTML, robots.txt и sitemap.xml.

Расписание и так висит в аудиториях и на сайте университета, а в приложении оно спрятано за
входом — поисковики его не видят. Здесь каждая группа получает свой адрес /raspisanie/<группа>
со своим <title>; страница собирается на сервере, без JavaScript. Студентов, отметки и коды
эти страницы не показывают. Выключить: PUBLIC_SCHEDULE=0.
"""

import re
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from app import db, schedule
from app.api.deps import SessionDep
from app.config import config

api = APIRouter(include_in_schema=False)

PAGE_CACHE = "public, max-age=300"   # расписание меняется редко, а поисковик и чат зайдут не раз
MAX_WEEKS = 26                       # листать дальше полугода незачем
KIND_SHORT = {"лекция": "лек.", "практика": "пр.", "семинар": "сем.", "лабораторная": "лаб."}


def today() -> date:
    return datetime.now(config.tz).date()


def site_url(request: Request) -> str:
    """Основной адрес сайта: DOMAIN из настроек, иначе тот, по которому пришёл запрос."""
    return config.site_url or str(request.base_url).rstrip("/")


def group_path(group: str) -> str:
    return "/raspisanie/" + quote(group, safe="")


def pretty_group(group: str) -> str:
    """«БИА 2 ПОДГРУППА» → «БИА · 2 подгруппа» — как в приложении."""
    m = re.match(r"^(.*?)\s*(\d+)\s*подгруппа$", group, re.I)
    return f"{m.group(1)} · {m.group(2)} подгруппа" if m else group


def _require_public() -> None:
    if not config.public_schedule:
        raise HTTPException(404, "Страница не найдена")


# --- robots.txt и sitemap.xml ------------------------------------------------------

@api.get("/robots.txt")
async def robots(request: Request):
    text = "User-agent: *\nAllow: /\nDisallow: /api/\n\n" f"Sitemap: {site_url(request)}/sitemap.xml\n"
    return PlainTextResponse(text, headers={"Cache-Control": "public, max-age=86400"})


@api.get("/sitemap.xml")
async def sitemap(request: Request, s: SessionDep):
    base = site_url(request)
    urls = [f"{base}/"]
    if config.public_schedule:
        urls.append(f"{base}/raspisanie")
        urls += [base + group_path(g) for g in await db.list_groups(s)]
    body = "".join(f"  <url><loc>{escape(u)}</loc></url>\n" for u in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' f"{body}</urlset>\n")
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


# --- страницы ----------------------------------------------------------------------

STYLE = """
:root{--page:#f2f2f7;--card:#fff;--text:#111;--hint:#6b7472;--accent:#1f6e6c;--hero:#2f6868;--sep:#e2e5e4;--warn:#b26a00}
@media (prefers-color-scheme:dark){:root{--page:#000;--card:#1c1c1e;--text:#f2f2f7;--hint:#9aa3a1;--accent:#6cc9c8;--sep:#2c2c2e;--warn:#ffb340}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text);font:17px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
a{color:var(--accent)}
.top{display:flex;align-items:center;gap:10px;font-weight:700;color:var(--hint);text-decoration:none;font-size:15px}
.top img{width:28px;height:28px;border-radius:8px}
h1{font-size:30px;line-height:1.15;margin:18px 0 6px}
.sub{color:var(--hint);margin:0 0 16px}
.weeknav{display:flex;justify-content:space-between;gap:12px;margin:0 0 18px;font-size:15px}
.day{background:var(--card);border-radius:18px;padding:6px 18px;margin:0 0 14px}
.day h2{font-size:17px;margin:12px 0 4px}
.day.today h2::after{content:" · сегодня";color:var(--accent)}
.lesson{display:flex;gap:14px;padding:10px 0;border-top:1px solid var(--sep)}
.day h2+.lesson,.day h2+.empty{border-top:0}
.time{flex:none;width:58px;font-variant-numeric:tabular-nums;font-weight:600}
.time small{display:block;color:var(--hint);font-weight:400}
.what b{display:block}
.meta{color:var(--hint);font-size:15px}
.note{color:var(--warn);font-size:15px}
.off{opacity:.55}.off b{text-decoration:line-through}
.empty{color:var(--hint);padding:10px 0}
.cta{background:var(--hero);color:#fff;border-radius:20px;padding:18px;margin:22px 0 0}
.cta a{color:#fff;font-weight:700}
ul.groups{list-style:none;padding:0;margin:0;background:var(--card);border-radius:18px}
ul.groups li{border-top:1px solid var(--sep)}ul.groups li:first-child{border-top:0}
ul.groups a{display:block;padding:14px 18px;text-decoration:none;color:var(--text)}
"""


def page(request: Request, title: str, description: str, body: str, path: str, noindex: bool = False) -> HTMLResponse:
    canonical = site_url(request) + path
    robots = '<meta name="robots" content="noindex, follow">\n' if noindex else ""
    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<link rel="canonical" href="{escape(canonical)}">
{robots}<meta property="og:type" content="website">
<meta property="og:title" content="{escape(title)}">
<meta property="og:description" content="{escape(description)}">
<meta property="og:url" content="{escape(canonical)}">
<meta property="og:image" content="{escape(site_url(request))}/icon-512.png">
<link rel="icon" href="/icon-192.png">
<style>{STYLE}</style>
</head>
<body><main>
<a class="top" href="/"><img src="/icon-192.png" alt="">{escape(config.app_name)}</a>
{body}
<div class="cta">Напоминание перед парой и уведомления об отменах и переносах — в приложении.
<a href="/">Открыть {escape(config.app_name)}</a></div>
</main></body>
</html>"""
    return HTMLResponse(html, headers={"Cache-Control": PAGE_CACHE})


@api.get("/raspisanie")
async def groups_page(request: Request, s: SessionDep):
    _require_public()
    groups = await db.list_groups(s)
    items = "".join(f'<li><a href="{group_path(g)}">{escape(pretty_group(g))}</a></li>' for g in groups)
    body = ('<h1>Расписание групп МПГУ</h1><p class="sub">Выбери группу — расписание на неделю откроется без входа.</p>'
            + (f'<ul class="groups">{items}</ul>' if groups else '<p class="sub">Групп пока нет.</p>'))
    return page(request, f"Расписание групп — {config.app_name}",
                "Расписание пар групп МПГУ на неделю: аудитории, преподаватели, переносы и отмены.", body, "/raspisanie")


def _lesson_html(l: schedule.Slot, cancelled: bool = False) -> str:
    meta = [KIND_SHORT.get((l.kind or "").lower(), l.kind)]
    meta.append(f"ауд. {l.room}" if l.room else "")
    meta.append(l.teacher)
    if l.half:
        meta.append(f"{l.half}-я половина группы")
    meta_text = " · ".join(escape(m) for m in meta if m)
    notes = []
    if cancelled:
        notes.append("Перенесена" if l.moved else "Отменена")
    elif l.status == "extra":
        notes.append("Перенос на этот день" if l.moved else "Разовая пара")
    elif l.status == "changed" and l.was:
        if "room" in l.was:
            notes.append(f"другая аудитория (вместо {l.was['room']})")
        if "start_time" in l.was:
            notes.append(f"другое время (было {l.was['start_time']})")
        if not notes:
            notes.append("изменена на этот день")
    if l.note:
        notes.append(l.note)
    note_html = f'<div class="note">{escape(" · ".join(notes))}</div>' if notes else ""
    end = f"<small>{escape(l.end_time)}</small>" if l.end_time else ""
    return (f'<div class="lesson{" off" if cancelled else ""}"><div class="time">{escape(l.start_time)}{end}</div>'
            f'<div class="what"><b>{escape(l.subject)}</b><div class="meta">{meta_text}</div>{note_html}</div></div>')


@api.get("/raspisanie/{group}")
async def group_page(group: str, request: Request, s: SessionDep, week: date | None = None):
    _require_public()
    groups = {g.casefold(): g for g in await db.list_groups(s)}
    name = groups.get(group.strip().casefold())
    if name is None:
        raise HTTPException(404, "Такой группы нет")
    now = today()
    mon = schedule.monday(week or now)
    if abs((mon - schedule.monday(now)).days) > MAX_WEEKS * 7:
        mon = schedule.monday(now)
    start = await db.get_semester_start(s)
    dates = [mon + timedelta(days=i) for i in range(7)]
    by_day = await schedule.group_days(s, name, dates, None)

    days_html = []
    for d in dates:
        day = by_day[d]
        if d.weekday() == 6 and not day.lessons and not day.cancelled:
            continue  # воскресенье без пар не показываем
        rows = sorted([(l, False) for l in day.lessons] + [(l, True) for l in day.cancelled],
                      key=lambda x: schedule.sort_key(x[0]))
        inner = "".join(_lesson_html(l, off) for l, off in rows) or '<div class="empty">Пар нет</div>'
        title = f"{schedule.WEEKDAYS[d.weekday()]}, {d.day} {schedule.MONTHS_GEN[d.month - 1]}"
        days_html.append(f'<section class="day{" today" if d == now else ""}"><h2>{escape(title)}</h2>{inner}</section>')

    number = schedule.week_number(mon, start)
    parity = "нечётная" if schedule.week_parity(mon, start) == schedule.WEEK_ODD else "чётная"
    last = dates[-1]
    period = f"{mon.day} {schedule.MONTHS_GEN[mon.month - 1]} — {last.day} {schedule.MONTHS_GEN[last.month - 1]}"
    path = group_path(name)
    nav = (f'<nav class="weeknav"><a rel="nofollow" href="{path}?week={(mon - timedelta(days=7)).isoformat()}">← прошлая неделя</a>'
           + (f'<a href="{path}">эта неделя</a>' if week else "")
           + f'<a rel="nofollow" href="{path}?week={(mon + timedelta(days=7)).isoformat()}">следующая →</a></nav>')
    shown = pretty_group(name)
    body = (f"<h1>Расписание {escape(shown)}</h1>"
            f'<p class="sub">{escape(f"{number}-я неделя · {parity} · {period}")}</p>{nav}{"".join(days_html)}'
            f'<p class="sub"><a href="/raspisanie">Все группы</a></p>')
    return page(request, f"Расписание {shown} — МПГУ",
                f"Расписание пар группы {shown} (МПГУ) на неделю: время, аудитории, преподаватели, переносы и отмены.",
                body, path, noindex=week is not None)
