#!/usr/bin/env python3
"""
Сбор данных для вкладок «Матчи», «Рейтинг» и карточек игроков.

Три файла на выходе:
  matches.js   — расписание ATP и WTA на неделю вперёд, сгруппировано по дням и турнирам
  rankings.js  — рейтинги ATP и WTA (top-500) плюс очки к защите на текущем турнире
  players.js   — справочник игроков: страна, возраст, рейтинг, фотография

Всё связывается по слагу игрока с tennisexplorer (/player/<slug>/) — он одинаков
и в расписании, и в рейтинге, поэтому клик по фамилии в любом месте сайта ведёт
в одну и ту же карточку.

Фотографии — из Wikimedia Commons: это единственный источник, который прямо
разрешает переиспользование. Ссылки складываются в кэш и повторно не ищутся,
за один запуск ищем не больше PHOTO_BATCH новых — чтобы не ловить 429.
"""

import io
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, timedelta

TE = "https://www.tennisexplorer.com"
WIKI = "https://en.wikipedia.org/w/api.php"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
WIKI_UA = {"User-Agent": "tennis-injury-terminal/1.0 (github pages project)"}

RANK_LIMIT = 500        # столько же строк, сколько показывает flashscore
MATCH_DAYS = 7          # горизонт расписания
PHOTO_BATCH = 40        # сколько новых фотографий искать за один запуск

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def get(url, headers=UA, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 + 2 * i)


def txt(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).replace("&nbsp;", " ").strip()


def norm(name):
    s = unicodedata.normalize("NFKD", name.lower().replace("-", " "))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return frozenset(w for w in re.split(r"[^a-z]+", s) if len(w) > 1)


# ─────────────────────────── рейтинги ───────────────────────────

def fetch_ranking(tour):
    """tour: atp-men | wta-women. Возвращает список строк рейтинга."""
    out, seen = [], set()
    for page in range(1, RANK_LIMIT // 50 + 2):
        url = "%s/ranking/%s/" % (TE, tour) + ("" if page == 1 else "?page=%d" % page)
        html = get(url)
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            rank = re.search(r">(\d+)\.<", row)
            link = re.search(r'/player/([^/"]+)/">([^<]+)<', row)
            if not (rank and link):
                continue
            r = int(rank.group(1))
            if r in seen or r > RANK_LIMIT:
                continue
            seen.add(r)
            cells = [txt(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            move = ""
            for c in cells[1:3]:
                if re.fullmatch(r"-?\d+", c or ""):
                    move = c
                    break
            country = next((c for c in cells
                            if re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)*|[A-Z]{2,4}", c or "")
                            and c not in link.group(2)), "")
            pts = next((c for c in reversed(cells) if re.fullmatch(r"\d{2,6}", c or "")), "")
            age = next((c for c in cells if re.fullmatch(r"[12]\d", c or "")), "")
            out.append({
                "rank": r, "slug": link.group(1), "name": link.group(2).strip(),
                "country": country, "age": int(age) if age else 0,
                "points": int(pts) if pts else 0, "move": int(move) if move else 0,
            })
        if len(seen) >= RANK_LIMIT:
            break
    return sorted(out, key=lambda x: x["rank"])


def fetch_defending(page_title):
    """«Points defending» из таблиц посева турнира на Википедии — по заголовку колонки."""
    html = get("https://en.wikipedia.org/wiki/" + urllib.parse.quote(page_title), WIKI_UA)
    out = {}
    for table in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
        if not rows:
            continue
        head = [txt(c).lower() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rows[0], re.S)]
        if not any("defending" in h for h in head):
            continue
        i_def = next(i for i, h in enumerate(head) if "defending" in h)
        i_name = next((i for i, h in enumerate(head) if "player" in h), None)
        if i_name is None:
            continue
        for row in rows[1:]:
            cells = [txt(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
            if len(cells) <= max(i_def, i_name):
                continue
            name, pts = cells[i_name], cells[i_def]
            pts = re.sub(r"[^\d]", "", pts.split("+")[0])
            if name and pts:
                out[norm(name)] = int(pts)
    return out


# ─────────────────────────── расписание ───────────────────────────

def fetch_day(day, kind):
    """kind: atp-single | wta-single. Матчи одного дня."""
    url = "%s/matches/?type=%s&year=%d&month=%02d&day=%02d" % (TE, kind, day.year, day.month, day.day)
    html = get(url)
    tour = "atp" if kind.startswith("atp") else "wta"
    out, event, slug = [], "", ""
    rows = re.findall(r'<tr[^>]*class="([^"]*)"[^>]*>(.*?)</tr>', html, re.S)
    pending = None
    for cls, row in rows:
        if "head" in cls:
            cell = re.search(r'<td class="t-name"[^>]*>(.*?)</td>', row, re.S)
            if cell:
                link = re.search(r'href="/([^/"]+)/', cell.group(1))
                name = txt(cell.group(1))
                if name and name.lower() != "main tournaments":
                    slug = link.group(1) if link else name.lower().replace(" ", "-")
                    event = name
            pending = None
            continue
        player = re.search(r'<td class="t-name"><a href="/player/([^/"]+)/">([^<]+)</a>\s*(?:\((\w+)\))?', row)
        if not player:
            continue
        who = {"slug": player.group(1), "name": player.group(2).strip(), "seed": player.group(3) or ""}
        scores = [txt(c) for c in re.findall(r'<td class="score[^"]*">(.*?)</td>', row)]
        scores = [s for s in scores if s and s != " "]
        if pending is None:
            tm = re.search(r'<td class="first time"[^>]*>(\d{1,2}:\d{2})', row)
            odds = re.findall(r'<td class="course"[^>]*>([\d.]+)</td>', row)
            pending = {
                "date": day.isoformat(), "tour": tour, "event": event, "eventSlug": slug,
                "time": tm.group(1) if tm else "", "p1": who, "s1": scores,
                "odds": odds[:2],
            }
        else:
            pending["p2"] = who
            pending["s2"] = scores
            out.append(pending)
            pending = None
    return [m for m in out if m.get("p2")]


def fetch_matches(today, known=None):
    days, seen = [], set()
    for i in range(MATCH_DAYS):
        d = today + timedelta(days=i)
        items = []
        for kind in ("atp-single", "wta-single"):
            try:
                items += fetch_day(d, kind)
            except Exception as e:
                print("  ! %s %s: %s" % (d, kind, e))
        kept = 0
        for m in items:
            if known and m["p1"]["slug"] not in known and m["p2"]["slug"] not in known:
                continue
            k = (m["date"], m["p1"]["slug"], m["p2"]["slug"])
            if k not in seen:
                seen.add(k)
                days.append(m)
                kept += 1
        print("  %s — матчей: %d, из них с игроками топ-500: %d" % (d.isoformat(), len(items), kept))
    return days


# ─────────────────────────── игроки ───────────────────────────

def wiki_photo(name):
    """Ищем страницу игрока и берём миниатюру. Пробуем и обратный порядок слов:
    в рейтинге фамилия идёт первой, а в Википедии — имя."""
    parts = name.split()
    for variant in ([name] + ([" ".join(reversed(parts))] if len(parts) == 2 else [])):
        url, title = _wiki_one(variant)
        if url:
            return url, title
    return None, None


def _wiki_one(name):
    try:
        q = urllib.parse.urlencode({"action": "query", "list": "search",
                                    "srsearch": name + " tennis", "srlimit": 1, "format": "json"})
        hits = json.loads(get(WIKI + "?" + q, WIKI_UA))["query"]["search"]
        if not hits:
            return None, None
        title = hits[0]["title"]
        q = urllib.parse.urlencode({"action": "query", "titles": title, "prop": "pageimages",
                                    "piprop": "thumbnail", "pithumbsize": 320, "format": "json"})
        page = list(json.loads(get(WIKI + "?" + q, WIKI_UA))["query"]["pages"].values())[0]
        return page.get("thumbnail", {}).get("source"), title
    except Exception:
        return None, None


def build_photos(prev, wanted, names):
    """{слаг: [ссылка, статья]}. Ищем порциями, найденное больше не трогаем."""
    photos = dict(prev)
    # неудачу не считаем окончательной: 429 от Википедии не должен навсегда
    # оставлять игрока без фотографии, поэтому пробуем ещё дважды в следующие разы
    missing = [s for s in wanted
               if s not in photos or (not photos[s][0] and (photos[s] + [0])[2] < 3)]
    for slug in missing[:PHOTO_BATCH]:
        url, title = wiki_photo(names.get(slug, slug.replace("-", " ")))
        if url:
            photos[slug] = [url, title]
        else:
            photos[slug] = [None, None, (photos.get(slug, [0, 0, 0]) + [0])[2] + 1]
        time.sleep(0.6)
    if missing:
        print("  фото: искали %d, осталось на потом %d, всего в кэше %d"
              % (min(len(missing), PHOTO_BATCH), max(0, len(missing) - PHOTO_BATCH), len(photos)))
    return photos


# ─────────────────────────── запись ───────────────────────────

def write_js(path, header, pairs):
    body = "\n".join("const %s = %s;\n" % (name, json.dumps(value, ensure_ascii=False, indent=0)
                                           .replace("\n", "")) for name, value in pairs)
    io.open(path, "w", encoding="utf-8", newline="").write(header + "\n" + body)
    print("  %s — %.0f КБ" % (path, len(body) / 1024))


def load_prev(path, name):
    try:
        src = io.open(path, encoding="utf-8").read()
        m = re.search(r"const %s = (.*?);\s*$" % name, src, re.S | re.M)
        return json.loads(m.group(1)) if m else {}
    except Exception:
        return {}


# ─────────────────────────── сборка ───────────────────────────

def news_players(data_js):
    """Имена игроков из ленты травм — им фотографии нужны в первую очередь."""
    return re.findall(r'who:"([^"]+)"', data_js)


def main():
    today = date.today()
    print("Рейтинги…")
    ranking = {"atp": fetch_ranking("atp-men"), "wta": fetch_ranking("wta-women")}
    print("  ATP %d, WTA %d" % (len(ranking["atp"]), len(ranking["wta"])))

    print("Очки к защите…")
    defend = {}
    for tour, page in (("atp", "2026 US Open – Men's singles"),
                       ("wta", "2026 US Open – Women's singles")):
        try:
            table = fetch_defending(page)
        except Exception as e:
            table = {}
            print("  ! %s: %s" % (page, e))
        hit = 0
        for row in ranking[tour]:
            pts = table.get(norm(row["name"]))
            if pts is not None:
                row["defend"] = pts
                hit += 1
        print("  %s — проставлено %d из %d строк таблицы" % (tour.upper(), hit, len(table)))

    print("Расписание…")
    matches = fetch_matches(today, set(r["slug"] for rows in ranking.values() for r in rows))
    print("  всего матчей: %d" % len(matches))

    print("Фотографии…")
    names = {r["slug"]: r["name"] for rows in ranking.values() for r in rows}
    try:
        data_js = io.open("data.js", encoding="utf-8").read()
    except Exception:
        data_js = ""
    by_norm = {norm(n): s for s, n in names.items()}
    wanted = [by_norm[norm(n)] for n in news_players(data_js) if norm(n) in by_norm]
    wanted += [r["slug"] for r in ranking["atp"][:60]] + [r["slug"] for r in ranking["wta"][:60]]
    wanted += [p["slug"] for m in matches for p in (m["p1"], m["p2"]) if p["slug"] in names]
    seen, ordered = set(), []
    for s_ in wanted:
        if s_ not in seen:
            seen.add(s_)
            ordered.append(s_)
    photos = build_photos(load_prev("players.js", "PHOTOS"), ordered, names)

    print("Карта сайта…")
    site = "https://grow000.github.io/tennis-injury-terminal/"
    sitemap = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <url>",
        "    <loc>%s</loc>" % site,
        "    <lastmod>%s</lastmod>" % today.isoformat(),
        "    <changefreq>hourly</changefreq>",
        "    <priority>1.0</priority>",
        "  </url>",
        "</urlset>",
        "",
    ])
    io.open("sitemap.xml", "w", encoding="utf-8", newline="").write(sitemap)
    print("  sitemap.xml обновлён")

    print("Запись…")
    stamp = "/* Собрано автоматически: extra.py. Руками не править. */"
    write_js("rankings.js", stamp, [("RANKING", ranking)])
    write_js("matches.js", stamp, [("MATCHES", matches)])
    write_js("players.js", stamp, [("PHOTOS", photos)])


if __name__ == "__main__":
    main()
