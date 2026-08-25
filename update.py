#!/usr/bin/env python3
"""
Обновление данных для Tennis Injury Terminal.

Что делает за один запуск:
  1. Тянет рейтинги ATP и WTA (топ-300) и проставляет свежие места всем игрокам.
  2. Тянет ленту травм и снятий RotoWire.
  3. Разбирает каждую новую запись: тур, турнир, рубрику, тип сигнала.
  4. Переводит её на русский по шаблонам (тексты RotoWire однотипные).
  5. Вставляет новые записи в NEWS, обновляет метку UPDATED и пишет data.js.

Перевод по правилам, а не машинный: у ленты десяток устойчивых конструкций,
и русская теннисная терминология для них однозначна. Всё, что разобрать не
удалось, помечается auto:true — сайт покажет такую запись по-английски,
а вы поправите текст руками.

Запуск:  python update.py              — обновить данные
         python update.py --dry-run    — показать, ничего не записывая
         python update.py --show-ru    — прогнать переводчик по всей ленте
"""

import io
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone, timedelta

DATA = "data.js"
UA = {"User-Agent": "Mozilla/5.0 (compatible; injury-terminal/1.0)"}
ROTOWIRE = "https://www.rotowire.com/tennis/news.php?view=injuries"
RANKINGS = {
    "atp": "https://www.tennisexplorer.com/ranking/atp-men/",
    "wta": "https://www.tennisexplorer.com/ranking/wta-women/",
}
RANK_PAGES = 6          # страницы по 50 строк — это топ-300
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}

try:                                    # windows-консоль иначе ломает кириллицу
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", "replace")


def key(name):
    """Имя в сравнимый вид: без диакритики, без дефисов, порядок слов не важен."""
    s = unicodedata.normalize("NFKD", name.lower().replace("-", " "))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return frozenset(w for w in re.split(r"[^a-z]+", s) if len(w) > 1)


def strip_tags(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


# ─────────────────────── словари для перевода ───────────────────────

BODY = {
    "abdomen": ("живота", "живот"), "back": ("спины", "спина"), "hip": ("бедра", "бедро"),
    "knee": ("колена", "колено"), "ankle": ("голеностопа", "голеностоп"),
    "shoulder": ("плеча", "плечо"), "wrist": ("запястья", "запястье"),
    "arm": ("руки", "рука"), "leg": ("ноги", "нога"), "hand": ("кисти", "кисть"),
    "elbow": ("локтя", "локоть"), "foot": ("стопы", "стопа"), "neck": ("шеи", "шея"),
    "thigh": ("бедра", "бедро"), "calf": ("икры", "икра"), "groin": ("паха", "пах"),
    "chest": ("груди", "грудь"), "ribs": ("рёбер", "рёбра"), "toe": ("пальца ноги", "палец ноги"),
    "finger": ("пальца", "палец"), "head": ("головы", "голова"), "hamstring": ("задней поверхности бедра", "бедро"),
    "shin": ("голени", "голень"), "achilles": ("ахиллова сухожилия", "ахиллово сухожилие"),
    "illness": ("недомогания", "недомогание"), "heat": ("перегрева", "перегрев"),
}
ROUND_GEN = {                       # «перед матчем …»
    "first round": "первого круга", "second round": "второго круга",
    "third round": "третьего круга", "fourth round": "четвёртого круга",
    "round of 16": "1/8 финала", "quarterfinals": "четвертьфинала",
    "quarterfinal": "четвертьфинала", "semifinals": "полуфинала",
    "semifinal": "полуфинала", "final": "финала",
}
ROUND_PREP = {                      # «в …»
    "first round": "в первом круге", "second round": "во втором круге",
    "third round": "в третьем круге", "fourth round": "в четвёртом круге",
    "round of 16": "в 1/8 финала", "quarterfinals": "в четвертьфинале",
    "quarterfinal": "в четвертьфинале", "semifinals": "в полуфинале",
    "semifinal": "в полуфинале", "final": "в финале",
}
ORD = {"first": "первого", "second": "второго", "third": "третьего", "fourth": "четвёртого"}
TOURN_RU = {
    "US Open": "US Open", "Cincinnati": "Цинциннати", "Winston-Salem": "Уинстон-Сейлем",
    "Monterrey": "Монтеррей", "Montreal": "Монреаль", "Toronto": "Торонто",
    "Australian Open": "Australian Open", "Roland Garros": "Ролан Гаррос",
    "Wimbledon": "Уимблдон", "Stuttgart": "Штутгарт",
}
TOURN_CASE = {
    "US Open": "US Open", "Цинциннати": "Цинциннати", "Уинстон-Сейлем": "Уинстон-Сейлема",
    "Монтеррей": "Монтеррея", "Монреаль": "Монреаля", "Торонто": "Торонто",
    "Australian Open": "Australian Open", "Ролан Гаррос": "Ролан Гаррос",
    "Уимблдон": "Уимблдона", "Штутгарт": "Штутгарта",
}
CODE = {
    "US Open": "USO", "Cincinnati": "CIN", "Winston-Salem": "WST", "Monterrey": "MTY",
    "Montreal": "MTL", "Toronto": "TOR", "Australian Open": "AO", "Roland Garros": "RG",
    "Wimbledon": "WIM", "Stuttgart": "STU",
}


def clean_event(name):
    """Снимаем артикль, год и хвост после запятой; «Winston-Salem Open» -> «Winston-Salem»."""
    name = re.sub(r"^(?:the )?(?:\d{4} )?", "", name.strip())
    name = re.sub(r"[,.].*$", "", name).strip()
    base = re.sub(r" Open$", "", name)
    return base if base != name and base in CODE else name


def tourn_of(text):
    """Название турнира из текста: сначала известные, потом «withdrew from X»."""
    for name in CODE:
        if re.search(r"\b" + re.escape(name), text):
            return name
    m = re.search(r"(?:withdrew from|is out for|retired .*? of) (?:the )?([A-Z][\w' -]+?)(?:[,.]| before| on| because| against| in )", text)
    return clean_event(m.group(1)) if m else ""


def code_of(name):
    if name in CODE:
        return CODE[name]
    letters = re.sub(r"[^A-Za-z ]", "", name).split()
    return ("".join(w[0] for w in letters)[:3] or "TBD").upper()


def hurt_ru(injury, case=0):
    k = injury.lower().strip()
    return BODY.get(k, (k, k))[case]


# ─────────────────────── разбор и перевод ───────────────────────

def classify(text, injury):
    t = text.lower()
    if "medical timeout" in t:
        return "injury", "mto"
    if "retired" in t and injury.lower() == "illness":
        return "injury", "illness"
    if "retired" in t:
        return "injury", "retired"
    if "suspend" in t or "banned" in t:
        return "condition", "condition"
    if injury.lower() == "rest":
        return "withdrawal", "rest"
    if re.search(r"entry list|is out for|will miss|is recovering|has withdrawn from", t):
        return "withdrawal", "wd_pre"
    return "injury" if injury.lower() not in ("undisclosed", "") else "withdrawal", "wd_mid"


def translate(text, injury, tour, names=None):
    """Возвращает (ru, ok). Текст начинается со сказуемого — имя подставит сайт."""
    names = names or {}
    f = tour == "wta"                                  # женский род
    sn = lambda base: base + ("ась" if f else "ся")    # снял-ся / снял-ась
    inj = injury.lower().strip()
    vague = inj in ("undisclosed", "")
    rest = inj == "rest"
    flag = lambda s: "<span class='flag'>%s</span>" % s
    person = lambda n: names.get(key(n), n.strip())

    def event(name):
        name = clean_event(name)
        ru = TOURN_RU.get(name, name)
        return "<b>%s</b>" % TOURN_CASE.get(ru, ru)

    def cause(prefix="из-за "):
        if vague:
            return ", причина не раскрыта"
        if rest:
            return ", причина указана как отдых"
        return " " + prefix + flag(hurt_ru(injury))

    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r",? (?:according to|per) [^.]*\.$", ".", t)     # «, according to X.»
    t = re.sub(r", [A-Z][^.]*? reports\.$", ".", t)             # «, X of Y reports.»
    t = re.sub(r"^\S+(?: \S+)? \([^)]*\) ", "", t)             # «Ruud (back) » в начале
    t = re.sub(r"^\S+(?: \S+)? (?=has |is |will |retired|withdrew)", "", t)

    # снялся по ходу матча
    m = re.match(r"retired down ([\d\-, ()]+?) to ([\w' .-]+?) in the ([\w ]+?) of ([\w' -]+?)(?: on \w+)?\.", t)
    if m:
        score, opp, rnd, ev = m.groups()
        score = score.replace(",", "").strip()
        rnd = ROUND_PREP.get(rnd.strip(), "в " + rnd.strip())
        if vague:
            body = "%s при %s против %s %s %s, причина не раскрыта." % (
                sn("снял"), score, person(opp), rnd, event(ev))
        else:
            reason = flag("недомогания") if inj == "illness" else flag("проблемой " + hurt_ru(injury))
            pre = "из-за " if inj == "illness" else "с "
            body = "%s %s%s при %s против %s %s %s." % (
                sn("снял"), pre, reason, score, person(opp), rnd, event(ev))
        return (body, True)

    # снялся перед матчем на турнире
    m = re.match(r"withdrew from ([\w' -]+?) before (?:his|her) ([\w ]+?) match(?: against ([\w' .-]+?))?\.", t)
    if m:
        ev, rnd, opp = m.groups()
        return ("%s с %s перед матчем %s%s%s." %
                (sn("снял"), event(ev), ROUND_GEN.get(rnd.strip(), rnd.strip()),
                 (" против " + person(opp)) if opp else "", cause()), True)

    # снялся с конкретного матча
    m = re.match(r"withdrew from (?:his|her) ([\w ]+?) match in ([\w' -]+?)(?: on \w+)?(?: against ([\w' .-]+?))?\.", t)
    if m:
        rnd, ev, opp = m.groups()
        return ("%s с матча %s в %s%s%s." %
                (sn("снял"), ROUND_GEN.get(rnd.strip(), rnd.strip()), event(ev),
                 (" против " + person(opp)) if opp else "", cause()), True)

    # снялся до турнира
    m = re.match(r"(?:is out for|has withdrawn from|will miss) (?:the )?([\w' -]+?)(?: because of[\w ]+)?\.", t)
    if m:
        return ("%s с %s%s." % (sn("снял"), event(m.group(1)), cause()), True)

    # медицинский перерыв
    if "medical timeout" in t:
        ev = tourn_of(t)
        return ("взял%s %s%s." % ("а" if f else "", flag("медицинский перерыв"),
                (" на турнире " + event(ev)) if ev else ""), True)

    return (text, False)


# ─────────────────────────── источники ───────────────────────────

def fetch_ranks(tour):
    out = {}
    for page in range(1, RANK_PAGES + 1):
        url = RANKINGS[tour] + ("" if page == 1 else "?page=%d" % page)
        try:
            html = get(url)
        except Exception as e:
            print("  ! рейтинг %s стр.%d: %s" % (tour.upper(), page, e))
            break
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            m = re.search(r">(\d+)\.<", row)
            n = re.search(r'/player/[^"]*/">([^<]+)<', row)
            if m and n:
                out.setdefault(key(n.group(1)), int(m.group(1)))
    return out


def fetch_news():
    html = get(ROTOWIRE)
    items = []
    for block in re.findall(r'<div class="news-update[ "].*?(?=<div class="news-update[ "]|<footer)', html, re.S):
        def one(pattern):
            m = re.search(pattern, block, re.S)
            return strip_tags(m.group(1)) if m else ""
        player = one(r'news-update__player-link[^>]*>(.*?)</a>')
        stamp = one(r'news-update__timestamp">(.*?)</div>')
        body = one(r'news-update__news">(.*?)</div>')
        hurt = one(r'news-update__inj">(.*?)</div>')
        d = re.match(r"(\w+) (\d+), (\d{4})", stamp)
        if not player or not d:
            continue
        items.append({
            "player": player, "injury": hurt, "text": body,
            "date": "%s-%02d-%02d" % (d.group(3), MONTHS.get(d.group(1), 1), int(d.group(2))),
        })
    return items


# ─────────────────────────── data.js ───────────────────────────

REC = re.compile(r'\{ts:"(?P<ts>[^"]+)",\s*who:"(?P<who>[^"]+)"(?P<rest>.*?)src:"', re.S)


def update_ranks(src, ranks):
    changed, missing = [], set()

    def sub(m):
        who, rest = m.group("who"), m.group("rest")
        tour = re.search(r'tour:"(\w+)"', rest)
        old = re.search(r"rank:(null|\d+)", rest)
        if not tour or not old or "/" in who:
            return m.group(0)
        new = ranks.get(tour.group(1), {}).get(key(who))
        if new is None:
            missing.add(who)
            return m.group(0)
        if str(new) != old.group(1):
            changed.append("%s: %s -> %d" % (who, old.group(1), new))
        return m.group(0).replace("rank:" + old.group(1), "rank:%d" % new, 1)

    return REC.sub(sub, src), changed, sorted(missing)


def known_names(src):
    """who -> whoRu из уже размеченных записей: имена не транслитерируем наугад."""
    out = {}
    for m in re.finditer(r'who:"([^"]+)", whoRu:"([^"]+)"', src):
        out[key(m.group(1))] = m.group(2)
    return out


def existing(src):
    return {(key(m.group("who")), m.group("ts")[:10]) for m in REC.finditer(src)}


def render(rec):
    fields = ['{ts:"%s", who:"%s"' % (rec["ts"], rec["who"])]
    fields.append('whoRu:"%s"' % rec["whoRu"] if rec["whoRu"] else "whoRu:null")
    fields.append('tour:"%s"' % rec["tour"])
    fields.append("rank:%s" % (rec["rank"] if rec["rank"] is not None else "null"))
    fields.append('code:"%s", kind:"%s", sig:"%s"' % (rec["code"], rec["kind"], rec["sig"]))
    head = "  " + ", ".join(fields) + (", auto:true" if rec["auto"] else "") + ","
    return (head + '\n   en:"%s",\n   ru:"%s",\n   src:"%s", srcName:"RotoWire"},'
            % (rec["en"].replace('"', "'"), rec["ru"].replace('"', "'"), ROTOWIRE))


def build(item, ranks, names):
    k = key(item["player"])
    tour = "wta" if ranks["wta"].get(k) else "atp"
    rank = ranks[tour].get(k)
    kind, sig = classify(item["text"], item["injury"])
    ev = tourn_of(item["text"])
    ru, ok = translate(item["text"], item["injury"], tour, names)
    # английский текст без «Фамилия (травма)» в начале — сайт подставляет имя сам
    en = re.sub(r"^\S+(?: \S+)? \([^)]*\) ", "", item["text"])
    en = re.sub(r"^\S+(?: \S+)? (?=has |is |will )", "", en)
    return {
        "ts": item["date"] + " 12:00", "who": item["player"], "whoRu": names.get(k),
        "tour": tour, "rank": rank, "code": code_of(ev), "kind": kind, "sig": sig,
        "en": en, "ru": ru if ok else en, "auto": not ok or not names.get(k),
    }


def insert(src, records):
    """Новые записи — в начало массива NEWS, он всё равно сортируется на сайте."""
    anchor = "const NEWS = [\n"
    i = src.index(anchor) + len(anchor)
    block = "\n".join(render(r) for r in records) + "\n"
    return src[:i] + block + src[i:]


def add_tournaments(src, records):
    """Незнакомый турнир — добавляем в TOURN, иначе на карточке будет голый код."""
    added = []
    for r in records:
        if r["code"] == "TBD" or ('%s:"' % r["code"]) in src:
            continue
        name = [n for n, c in CODE.items() if c == r["code"]]
        label = name[0] if name else r["code"]
        src = src.replace("const TOURN = {\n", 'const TOURN = {\n  %s:"%s",\n' % (r["code"], label), 1)
        added.append(r["code"])
    return src, added


def main():
    dry = "--dry-run" in sys.argv
    src = io.open(DATA, encoding="utf-8").read()

    if "--show-ru" in sys.argv:
        ranks = {t: fetch_ranks(t) for t in RANKINGS}
        names = known_names(src)
        for it in fetch_news():
            r = build(it, ranks, names)
            print("%s %s [%s/%s]%s\n  EN %s\n  RU %s\n" %
                  (r["ts"][:10], r["who"], r["kind"], r["sig"],
                   "  ← не разобрано" if r["auto"] else "", r["en"], r["ru"]))
        return

    print("Рейтинги…")
    ranks = {t: fetch_ranks(t) for t in RANKINGS}
    print("  ATP %d строк, WTA %d строк" % (len(ranks["atp"]), len(ranks["wta"])))
    src, changed, missing = update_ranks(src, ranks)
    print("  обновлено мест: %d" % len(changed))
    for c in changed:
        print("    " + c)
    if missing:
        print("  вне топ-%d (оставлено как есть): %s" % (RANK_PAGES * 50, ", ".join(missing)))

    print("\nRotoWire…")
    try:
        news = fetch_news()
    except Exception as e:
        news, changed = [], changed
        print("  ! не удалось получить ленту: %s" % e)
    have = existing(src)
    names = known_names(src)
    fresh = [build(n, ranks, names) for n in news if (key(n["player"]), n["date"]) not in have]
    print("  в ленте %d записей, новых %d" % (len(news), len(fresh)))
    for r in fresh:
        print("    + %s %s [%s/%s]%s" % (r["ts"][:10], r["who"], r["kind"], r["sig"],
                                         "  ← нужен ручной перевод" if r["auto"] else ""))

    if fresh:
        src, added = add_tournaments(src, fresh)
        if added:
            print("  новые турниры в TOURN: %s" % ", ".join(added))
        src = insert(src, fresh)

    now = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    src = re.sub(r'const UPDATED = "[^"]*"', 'const UPDATED = "%s"' % now, src)

    if dry:
        print("\n--dry-run: файл не изменён")
    else:
        io.open(DATA, "w", encoding="utf-8", newline="").write(src)
        print("\n%s записан, UPDATED = %s" % (DATA, now))


if __name__ == "__main__":
    main()
