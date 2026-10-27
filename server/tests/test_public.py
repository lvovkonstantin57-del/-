"""Страницы без входа: расписание группы в HTML, robots.txt, sitemap.xml."""

from dataclasses import replace
from datetime import date

from app.api import public


async def test_robots_and_sitemap(client, seeded):
    r = await client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /api/" in r.text
    assert "Sitemap: http://t/sitemap.xml" in r.text

    r = await client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<loc>http://t/</loc>" in r.text and "<loc>http://t/raspisanie</loc>" in r.text
    assert "<loc>http://t/raspisanie/%D0%93%D0%A0%201</loc>" in r.text  # «ГР 1»


async def test_group_page_is_plain_html(client, seeded, monkeypatch):
    monkeypatch.setattr(public, "today", lambda: date(2026, 9, 7))  # понедельник, 2-я неделя (чётная)
    r = await client.get("/raspisanie/ГР 1")
    assert r.status_code == 200 and r.headers["cache-control"].startswith("public")
    html = r.text
    assert '<html lang="ru">' in html and "<title>Расписание ГР 1 — МПГУ</title>" in html
    assert '<meta name="description"' in html and 'rel="canonical" href="http://t/raspisanie/%D0%93%D0%A0%201"' in html
    # Пары — прямо в разметке, без JavaScript: чётная неделя — зоология, не ботаника
    assert "Физкультура" in html and "Зоология" in html and "Ботаника" not in html
    assert "ауд. Спортзал" in html and "Петров П. П." in html
    assert "2-я неделя · чётная" in html and "<script" not in html
    # Студентов и кодов здесь нет
    assert "Тестов" not in html
    # Другая неделя — та же страница, но не для индекса
    other = await client.get("/raspisanie/ГР 1", params={"week": "2026-09-14"})
    assert "Ботаника" in other.text and 'content="noindex, follow"' in other.text
    # Регистр не важен, неизвестной группы нет
    assert (await client.get("/raspisanie/гр 1")).status_code == 200
    assert (await client.get("/raspisanie/Нет такой")).status_code == 404

    groups = await client.get("/raspisanie")
    assert 'href="/raspisanie/%D0%93%D0%A0%201"' in groups.text and "ГР 2" in groups.text


async def test_public_schedule_can_be_turned_off(client, seeded, monkeypatch):
    monkeypatch.setattr(public, "config", replace(public.config, public_schedule=False))
    assert (await client.get("/raspisanie/ГР 1")).status_code == 404
    assert (await client.get("/raspisanie")).status_code == 404
    assert "/raspisanie" not in (await client.get("/sitemap.xml")).text


async def test_domain_goes_into_links(client, seeded, monkeypatch):
    monkeypatch.setattr(public, "config", replace(public.config, domain="mpgu-timetable.ru"))
    assert "Sitemap: https://mpgu-timetable.ru/sitemap.xml" in (await client.get("/robots.txt")).text
    assert "<loc>https://mpgu-timetable.ru/raspisanie</loc>" in (await client.get("/sitemap.xml")).text


async def test_index_has_description(client, database):
    html = (await client.get("/")).text
    assert '<html lang="ru">' in html and '<meta name="description"' in html and 'href="/raspisanie"' in html
