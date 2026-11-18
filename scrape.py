#!/usr/bin/env python3
"""Povlači natječaje za specijalizacije doktora medicine i sprema ih u natjecaji.json.

Izvor 1 (glavni): https://spec.edo.hr/  – popis oglasa iz Narodnih novina (osvježava se 2x dnevno)
Izvor 2 (dopuna): stranica Ministarstva zdravstva s PDF-ovima natječaja (ako nešto nije u izvoru 1)

Pravila:
  * poštuje robots.txt (stranica koja se ne smije čitati preskače se, natječaj ostaje u popisu s poveznicom)
  * jedan zahtjev u sekundi, samo za natječaje koje još nema u spremniku
  * ako se tekst natječaja ne može pročitati, natječaj se ipak upisuje (bez specijalizacija) da ništa ne promakne
"""
import argparse, datetime as dt, hashlib, html, json, os, re, sys, time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests

UA = 'SpecijalizacijePratitelj/1.0 (personal tool; few requests; respects robots.txt)'
MINISTRY_INDEX = ('https://zdravlje.gov.hr/o-ministarstvu/djelokrug-1297/kutak-za-djelatnike-u-zdravstvu/'
                  'specijalisticko-usavrsavanje-za-doktore-medicine-1350/natjecaji-za-specijalizaciju-7155/7155')

# ----------------------------------------------------------------------------- pomoćne
def norm(s):
    s = (s or '').lower()
    for a, b in (('č', 'c'), ('ć', 'c'), ('š', 's'), ('ž', 'z'), ('đ', 'd')):
        s = s.replace(a, b)
    return s


def stem_tokens(s):
    return {w[:3] for w in re.findall(r'[a-z0-9]+', norm(s)) if len(w) > 2}


CANON = [
    'Abdominalna kirurgija', 'Alergologija i klinička imunologija',
    'Anesteziologija, reanimatologija i intenzivna medicina', 'Dermatologija i venerologija',
    'Dječja i adolescentna psihijatrija', 'Dječja kirurgija', 'Endokrinologija i dijabetologija',
    'Epidemiologija', 'Fizikalna medicina i rehabilitacija', 'Gastroenterologija', 'Gerijatrija',
    'Ginekologija i opstetricija', 'Hematologija', 'Hitna medicina', 'Infektologija',
    'Internistička onkologija', 'Javnozdravstvena medicina', 'Kardiologija', 'Kardiotorakalna kirurgija',
    'Klinička farmakologija s toksikologijom', 'Klinička mikrobiologija', 'Klinička radiologija',
    'Laboratorijska imunologija', 'Maksilofacijalna kirurgija', 'Medicina rada i sporta',
    'Medicinska genetika', 'Nefrologija', 'Neurokirurgija', 'Neurologija', 'Nuklearna medicina',
    'Obiteljska medicina', 'Oftalmologija i optometrija', 'Onkologija i radioterapija',
    'Opća interna medicina', 'Opća kirurgija', 'Ortopedija i traumatologija', 'Otorinolaringologija',
    'Patologija i citologija', 'Pedijatrija', 'Plastična, rekonstrukcijska i estetska kirurgija',
    'Pulmologija', 'Psihijatrija', 'Reumatologija', 'Školska i adolescentna medicina', 'Sudska medicina',
    'Torakalna kirurgija', 'Transfuzijska medicina', 'Urologija', 'Vaskularna kirurgija',
]
CANON_TOK = [(c, stem_tokens(c)) for c in CANON]


def canon_spec(raw):
    """Genitiv iz natječaja ('hitne medicine') -> nominativ ('Hitna medicina'); nepoznato ostaje kako piše."""
    raw = re.sub(r'\s+', ' ', raw).strip(' .;,')
    t = stem_tokens(raw)
    if not t:
        return raw
    exact = [c for c, ct in CANON_TOK if ct == t]
    if exact:
        return exact[0]
    if len(t) >= 2:
        sub = [(len(ct - t), c) for c, ct in CANON_TOK if t <= ct]
        if sub:
            return sorted(sub)[0][1]
    return raw[:1].upper() + raw[1:]


ZUP_STEMS = [
    ('bjelovarsko-bilogorsk', 'Bjelovarsko-bilogorska'), ('brodsko-posavsk', 'Brodsko-posavska'),
    ('dubrovacko-neretvansk', 'Dubrovačko-neretvanska'), ('istarsk', 'Istarska'), ('karlovack', 'Karlovačka'),
    ('koprivnicko-krizevack', 'Koprivničko-križevačka'), ('krapinsko-zagorsk', 'Krapinsko-zagorska'),
    ('licko-senjsk', 'Ličko-senjska'), ('medjimursk', 'Međimurska'), ('osjecko-baranjsk', 'Osječko-baranjska'),
    ('pozesko-slavonsk', 'Požeško-slavonska'), ('primorsko-goransk', 'Primorsko-goranska'),
    ('sisacko-moslavack', 'Sisačko-moslavačka'), ('splitsko-dalmatinsk', 'Splitsko-dalmatinska'),
    ('sibensko-kninsk', 'Šibensko-kninska'), ('varazdinsk', 'Varaždinska'),
    ('virovitičko-podravsk', 'Virovitičko-podravska'), ('virovitico-podravsk', 'Virovitičko-podravska'),
    ('vukovarsko-srijemsk', 'Vukovarsko-srijemska'), ('zadarsk', 'Zadarska'), ('zagrebacke zupanij', 'Zagrebačka'),
    ('zagrebacka zupanij', 'Zagrebačka'),
]
CITY = [
    ('slavonski brod', 'Brodsko-posavska'), ('nova gradiska', 'Brodsko-posavska'), ('slavonski', 'Brodsko-posavska'),
    ('krapinske toplice', 'Krapinsko-zagorska'), ('zabok', 'Krapinsko-zagorska'), ('varazdinske toplice', 'Varaždinska'),
    ('varazdin', 'Varaždinska'), ('cakovec', 'Međimurska'), ('koprivnic', 'Koprivničko-križevačka'),
    ('bjelovar', 'Bjelovarsko-bilogorska'), ('virovitic', 'Virovitičko-podravska'), ('pozega', 'Požeško-slavonska'),
    ('lipik', 'Požeško-slavonska'), ('vinkovci', 'Vukovarsko-srijemska'), ('vukovar', 'Vukovarsko-srijemska'),
    ('osijek', 'Osječko-baranjska'), ('nasice', 'Osječko-baranjska'), ('sisak', 'Sisačko-moslavačka'),
    ('topusko', 'Sisačko-moslavačka'), ('popovaca', 'Sisačko-moslavačka'), ('karlovac', 'Karlovačka'),
    ('gospic', 'Ličko-senjska'), ('rijeka', 'Primorsko-goranska'), ('crikvenica', 'Primorsko-goranska'),
    ('opatij', 'Primorsko-goranska'), ('lovran', 'Primorsko-goranska'), ('rab', 'Primorsko-goranska'),
    ('pula', 'Istarska'), ('zadar', 'Zadarska'), ('sibenik', 'Šibensko-kninska'), ('knin', 'Šibensko-kninska'),
    ('split', 'Splitsko-dalmatinska'), ('makarska', 'Splitsko-dalmatinska'), ('dubrovnik', 'Dubrovačko-neretvanska'),
    ('gornja bistra', 'Zagrebačka'), ('bistra', 'Zagrebačka'), ('zapresic', 'Zagrebačka'),
    ('sestre milosrdnice', 'Grad Zagreb'), ('dubrava', 'Grad Zagreb'), ('merkur', 'Grad Zagreb'),
    ('sveti duh', 'Grad Zagreb'), ('vrapce', 'Grad Zagreb'), ('srebrnjak', 'Grad Zagreb'),
    ('fran mihaljevic', 'Grad Zagreb'), ('sveti ivan', 'Grad Zagreb'), ('stampar', 'Grad Zagreb'),
    ('hrvatski zavod za', 'Grad Zagreb'), ('zagreb', 'Grad Zagreb'),
]


def guess_zupanija(ustanova):
    n = norm(ustanova)
    for stem, name in ZUP_STEMS:
        if stem in n:
            return name
    for stem, name in CITY:
        if re.search(r'(?<![a-z])' + re.escape(stem), n):
            return name
    return ''


MJESECI = {'sijecnja': 1, 'veljace': 2, 'ozujka': 3, 'travnja': 4, 'svibnja': 5, 'lipnja': 6, 'srpnja': 7,
           'kolovoza': 8, 'rujna': 9, 'listopada': 10, 'studenoga': 11, 'studenog': 11, 'prosinca': 12}


def to_iso(d, m, y):
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return ''


def add_days(iso, n):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=n)).isoformat()


# ----------------------------------------------------------------------------- mreža
class Net:
    def __init__(self, delay=1.0, respect_robots=True):
        self.s = requests.Session()
        self.s.headers['User-Agent'] = UA
        self.delay = delay
        self.last = 0.0
        self.respect = respect_robots
        self.robots = {}

    def allowed(self, url):
        if not self.respect:
            return True
        p = urlparse(url)
        base = f'{p.scheme}://{p.netloc}'
        if base not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(base + '/robots.txt', timeout=20)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp.parse([])
            except requests.RequestException:
                rp.parse([])
            self.robots[base] = rp
        return self.robots[base].can_fetch(UA, url)

    def get(self, url, binary=False):
        if not self.allowed(url):
            raise PermissionError('robots.txt ne dopušta: ' + url)
        wait = self.delay - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        last_err = None
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=40)
                self.last = time.time()
                if r.status_code == 200:
                    if binary:
                        return r.content
                    try:
                        return r.content.decode('utf-8')
                    except UnicodeDecodeError:
                        return r.content.decode(r.apparent_encoding or 'cp1250', errors='replace')
                last_err = f'HTTP {r.status_code}'
                if r.status_code in (403, 404, 410):
                    break
            except requests.RequestException as e:
                last_err = str(e)
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f'{url}: {last_err}')


# ----------------------------------------------------------------------------- HTML -> tekst
BLOCK = re.compile(r'</?(p|div|li|ul|ol|br|tr|table|h[1-6]|section|article|td|th)\b[^>]*>', re.I)


def html_to_text(h):
    h = re.sub(r'(?is)<(script|style|noscript|head)\b.*?</\1>', ' ', h)
    h = BLOCK.sub('\n', h)
    h = re.sub(r'(?s)<[^>]+>', ' ', h)
    h = html.unescape(h).replace('\xa0', ' ')
    lines = [re.sub(r'[ \t]+', ' ', l).strip() for l in h.split('\n')]
    return '\n'.join(l for l in lines if l)


def pdf_to_text(data):
    try:
        from pypdf import PdfReader
        import io
        r = PdfReader(io.BytesIO(data))
        txt = '\n'.join((p.extract_text() or '') for p in r.pages)
        # prijelom riječi na kraju retka ("speci-\njalizaciju") nije u tekstu, ali spoji ako postoji
        return re.sub(r'-\n(?=[a-zčćđšž])', '', txt)
    except Exception:
        return ''


# ----------------------------------------------------------------------------- popis (spec.edo.hr)
A_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
NN_RE = re.compile(r'NN\s*(\d+)\s*/\s*(\d{4})[^<\n]{0,40}?(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})')


def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def split_title(title):
    """'Opća bolnica X - ponovljeni natječaj' -> ('Opća bolnica X', 'ponovljeni natječaj')"""
    m = re.split(r'\s+[-–—]\s+|\s*\(\s*(?=ponovljen|ispravak|poništ)', title, maxsplit=1, flags=re.I)
    if len(m) == 2 and re.search(r'ponovljen|ispravak|poništ|natječaj', m[1], re.I):
        return m[0].strip(), m[1].strip(' )')
    return title.strip(), ''


def vrsta_of(label):
    l = norm(label)
    if 'ponisten' in l and 'natjecaj i' in l:
        return 'natjecaj_ponistenje'
    if 'ponisten' in l:
        return 'ponistenje'
    if 'ispravak' in l:
        return 'ispravak'
    if 'ponovljen' in l:
        return 'ponovljeni'
    return 'natjecaj'


def parse_index(html_text, base_url):
    anchors = [(m.start(), m.end(), m.group(1), clean(m.group(2))) for m in A_RE.finditer(html_text)]
    starts = [i for i, a in enumerate(anchors) if '/clanci/oglasi/' in a[2]]
    items, seen = [], set()
    for k, i in enumerate(starts):
        s = anchors[i][0]
        e = anchors[starts[k + 1]][0] if k + 1 < len(starts) else len(html_text)
        href = urljoin(base_url, anchors[i][2])
        m = re.search(r'/o(\d+)\.html', href)
        if not m:
            continue
        oid = m.group(1)
        title = anchors[i][3]
        if oid in seen or not title or title.startswith('http'):
            # ponovljeni anchor s golim URL-om – naslov uzmi iz prvog pojavljivanja
            continue
        region = html_text[s:e]
        nn = NN_RE.search(clean(region))
        if not nn:
            continue
        pdf = ''
        for j in range(i + 1, len(anchors)):
            if anchors[j][0] >= e:
                break
            if re.search(r'/pdf\b|\.pdf\b', anchors[j][2], re.I):
                pdf = urljoin(base_url, anchors[j][2])
                break
        seen.add(oid)
        ustanova, label = split_title(title)
        items.append({
            'id': oid, 'naslov': title, 'ustanova': ustanova, 'vrsta': vrsta_of(label or title),
            'nn': f'NN {nn.group(1)}/{nn.group(2)}', 'objavljen': to_iso(nn.group(3), nn.group(4), nn.group(5)),
            'link': href, 'pdf': pdf, 'izvor': 'spec.edo.hr',
        })
    return items


# ----------------------------------------------------------------------------- popis (Ministarstvo)
def parse_ministry(html_text, base_url):
    items, seen = [], set()
    for m in A_RE.finditer(html_text):
        href = urljoin(base_url, m.group(1))
        mm = re.search(r'/UserDocsImages/(\d{4})_Specijalizacije/(\d{4})\.(\d{2})\.(\d{2})\._([A-Za-z]+)_([A-Za-z0-9]+)\.pdf', href)
        if not mm:
            continue
        tip = mm.group(5).lower()
        if not tip.startswith('natjecaj') or href in seen:
            continue
        seen.add(href)
        label = clean(m.group(2))
        ustanova, _ = split_title(label)
        items.append({
            'id': 'm' + hashlib.sha1(href.encode()).hexdigest()[:8], 'naslov': label,
            'ustanova': (ustanova.lower()[:1].upper() + ustanova.lower()[1:]) if ustanova.isupper() else ustanova, 'vrsta': 'natjecaj', 'nn': '',
            'objavljen': to_iso(mm.group(4), mm.group(3), mm.group(2)), 'link': href, 'pdf': href,
            'izvor': 'zdravlje.gov.hr',
        })
    return items


def same_notice(a, b):
    ta, tb = stem_tokens(a['ustanova']), stem_tokens(b['ustanova'])
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    if overlap < 0.7 or not a['objavljen'] or not b['objavljen']:
        return False
    d = abs((dt.date.fromisoformat(a['objavljen']) - dt.date.fromisoformat(b['objavljen'])).days)
    return d <= 10


# ----------------------------------------------------------------------------- razbor oglasa
NUMW = {'jedan': 1, 'jedna': 1, 'dva': 2, 'dvije': 2, 'tri': 3, 'četiri': 4, 'pet': 5, 'šest': 6, 'sedam': 7,
        'osam': 8, 'devet': 9, 'deset': 10}
NON_DOCTOR = ('inženjer', 'sestr', 'tehničar', 'magistar', 'mag.', 'farmac', 'ljekarn', 'biokemij', 'njegovatelj',
              'radno mjesto', 'na određeno', 'dentalne', 'stomatolog', 'fizioterapeut', 'psiholog')
ITEM_RE = re.compile(
    r'^[\s\-–—•*·]*(?:\d{1,2}[.)]\s*)?(?P<spec>[A-Za-zČĆĐŠŽčćđšž][^–—:\n]{2,140}?)\s*[–—:-]\s*'
    r'(?P<n>\d{1,3}|[a-zčćšž]+)\s*(?:\([^)]{0,25}\))?\s*(?:/\s*)?(?:izvr[sš]itelj|specijali[zs]acij|radn\w+ mjest|mjest)', re.I)
PREFACE_RE = re.compile(r'(?:\biz\s*:|\bza\s*:|\biz\s+(?=[a-zčćđšž]))(?P<rest>.*)$', re.I)
NEWSPAPER = re.compile(r've[cč]ernj|jutarnj|slobodn\w+ dalmacij|novom listu|dnevnom listu|dnevnom tisku|glas\w* slavonij', re.I)


def parse_specs(text):
    lines = [l.strip() for l in re.split(r'\n|;\s*(?=\d|[–-])', text) if l.strip()]
    specs, in_block = [], False
    for ln in lines:
        low = ln.lower()
        cand = ln
        if re.match(r'^\s*iz\s+[a-zčćđšž]', ln, re.I):
            in_block = True
            cand = re.sub(r'^\s*iz\s+', '', ln, flags=re.I)
        elif re.search(r'speci[jž]ali[zs]|specijalistič', low):
            m = PREFACE_RE.search(ln)
            if m:
                in_block = True
                cand = m.group('rest')
        m = ITEM_RE.match(cand)
        if m and (in_block or canon_spec(m.group('spec')) in CANON):
            raw = m.group('spec').strip(' .,:;')
            raw = re.sub(r'^(?:na|iz|za)\s+', '', raw, flags=re.I)
            if any(w in raw.lower() for w in NON_DOCTOR) or len(raw.split()) > 9:
                continue
            n = m.group('n').lower()
            cnt = int(n) if n.isdigit() else NUMW.get(n)
            specs.append({'naziv': raw, 'kanon': canon_spec(raw), 'n': cnt})
        elif in_block and specs and not re.match(r'^[\s\-–—•*·\d.)]', ln):
            in_block = False
    # ukloni duplikate
    out, seen = [], set()
    for s in specs:
        k = (norm(s['kanon']), s['n'])
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def parse_rok(text, objavljen):
    """vrati (rok_iso, procijenjeno, napomena)"""
    napomena = ''
    flat = re.sub(r'\s+', ' ', text)
    # izričit datum
    m = re.search(r'rok[^.]{0,100}?(?:do|zaključno(?: do)?)\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})', flat, re.I)
    if m:
        iso = to_iso(m.group(1), m.group(2), m.group(3))
        if iso:
            return iso, False, ''
    best = None
    for m in re.finditer(r'(\d{1,2})\s*(?:\([^)]{0,20}\)\s*)?dana', flat):
        ctx = flat[max(0, m.start() - 110): m.end() + 70].lower()
        if not re.search(r'prijav|ponud', ctx):
            continue
        if re.search(r'isteka roka|razgovor|odluk\w+ (?:o izboru )?(?:biti|bit|se)|objav\w+ (?:liste|rezultata)|testiranj|ugovor', ctx[:ctx.find(m.group(0).lower()) + 60]):
            # odluka/razgovor, a ne rok za prijavu
            if not re.search(r'rok', ctx[:ctx.find(m.group(0).lower())]):
                continue
        days = int(m.group(1))
        if 3 <= days <= 60:
            best = (days, ctx)
            break
    if not best:
        return '', True, ''
    days, ctx = best
    if NEWSPAPER.search(ctx):
        napomena = 'Rok se računa od objave u dnevnom listu – provjeri točan datum u natječaju.'
    if not objavljen:
        return '', True, napomena
    return add_days(objavljen, days), True, napomena


def parse_oglas(text, item):
    specs = parse_specs(text)
    rok, proc, nap = parse_rok(text, item.get('objavljen', ''))
    vrsta = item['vrsta']
    if re.search(r'poništava se|poništenj', text, re.I) and not specs:
        vrsta = 'ponistenje'
    return {'specs': specs, 'rok': rok, 'rokProcijenjen': proc, 'rokNapomena': nap, 'vrsta': vrsta}


# ----------------------------------------------------------------------------- glavni tok
def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def enrich(net, item, log):
    text = ''
    err = ''
    if item['izvor'] == 'spec.edo.hr':
        full = item['link'] if '/full/' in item['link'] else item['link'].replace('/clanci/oglasi/', '/clanci/oglasi/full/')
        for url in (full, item['link']):
            try:
                text = html_to_text(net.get(url))
                if len(text) > 200:
                    break
            except (PermissionError, RuntimeError) as e:
                err = str(e)
        if len(text) < 200 and item.get('pdf'):
            try:
                text = pdf_to_text(net.get(item['pdf'], binary=True))
            except (PermissionError, RuntimeError) as e:
                err = str(e)
    if len(text) < 200:
        log.append(f"  ! tekst nedostupan za {item['ustanova']} ({item['link']}) {err}")
        item.update({'specs': [], 'rok': '', 'rokProcijenjen': True, 'rokNapomena': '', 'parsed': False})
        return item
    res = parse_oglas(text, item)
    item.update(res)
    item['parsed'] = bool(res['specs']) or res['vrsta'] == 'ponistenje'
    if not res['specs'] and res['vrsta'] != 'ponistenje':
        log.append(f"  ? specijalizacije nisu prepoznate: {item['ustanova']} ({item['link']})")
    return item


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.json')
    ap.add_argument('--data', default='data/natjecaji.json')
    ap.add_argument('--out', default='site/natjecaji.json')
    ap.add_argument('--issue', default='new_items.md')
    ap.add_argument('--index-url')
    ap.add_argument('--ministry-url')
    ap.add_argument('--no-robots', action='store_true')
    ap.add_argument('--delay', type=float, default=1.0)
    a = ap.parse_args()

    cfg = load_json(a.config, {})
    index_url = a.index_url or cfg.get('index_url') or 'https://spec.edo.hr/'
    use_ministry = cfg.get('ministry', True) if not a.index_url else bool(a.ministry_url)
    ministry_url = a.ministry_url or MINISTRY_INDEX
    net = Net(a.delay, respect_robots=not a.no_robots)
    log = []

    prev = load_json(a.data, {'natjecaji': []})
    known = {n['id']: n for n in prev.get('natjecaji', [])}
    today = dt.date.today().isoformat()

    # --- izvor 1
    try:
        primary = parse_index(net.get(index_url), index_url)
    except Exception as e:
        print(f'GREŠKA: ne mogu dohvatiti popis {index_url}: {e}', file=sys.stderr)
        return 2
    if not primary:
        print('GREŠKA: popis je dohvaćen, ali u njemu nisam našao nijedan oglas – vjerojatno se promijenio izgled stranice.', file=sys.stderr)
        return 3
    log.append(f'spec.edo.hr: {len(primary)} oglasa u popisu')

    # --- izvor 2 (dopuna)
    secondary = []
    if use_ministry:
        try:
            secondary = parse_ministry(net.get(ministry_url), ministry_url)
            log.append(f'zdravlje.gov.hr: {len(secondary)} natječaja u popisu')
        except Exception as e:
            log.append(f'  ! Ministarstvo nedostupno ({e}) – preskačem dopunu')
    extra = [s for s in secondary if not any(same_notice(s, p) for p in primary)]
    log.append(f'  od toga samo na Ministarstvu: {len(extra)}')

    result, new_ids = [], []
    for item in primary + extra:
        old = known.get(item['id'])
        if old and (old.get('parsed') or old.get('pokusaja', 0) >= 3) and old.get('link') == item['link']:
            old['naslov'] = item['naslov']
            result.append(old)
            continue
        item = enrich(net, item, log)
        item['pokusaja'] = (old or {}).get('pokusaja', 0) + (0 if item.get('parsed') else 1)
        item['zupanija'] = guess_zupanija(item['ustanova'])
        item['prviPut'] = (old or {}).get('prviPut', today)
        result.append(item)
        if not old:
            new_ids.append(item['id'])

    result.sort(key=lambda n: (n.get('objavljen') or '', n['id']), reverse=True)
    out = {'generated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'izvori': [index_url] + ([ministry_url] if use_ministry else []), 'natjecaji': result}
    for path in (a.out, a.data):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)

    # --- obavijest o novima
    new = [n for n in result if n['id'] in new_ids and n['vrsta'] not in ('ponistenje',)]
    wspec, wzup = cfg.get('watch_specs') or [], cfg.get('watch_zupanije') or []
    if wspec or wzup:
        def hit(n):
            if n.get('zupanija') in wzup:
                return True
            toks = [stem_tokens(w) for w in wspec]
            return any(stem_tokens(s['kanon']) >= t or t >= stem_tokens(s['kanon']) for s in n['specs'] for t in toks if t)
        new = [n for n in new if hit(n)]
    if new and prev.get('natjecaji'):  # prvi put (prazan spremnik) ne šalji lavinu obavijesti
        lines = []
        for n in new:
            sp = ', '.join(f"{s['kanon']}" + (f" ({s['n']})" if s.get('n') else '') for s in n['specs']) or '_specijalizacije nisu prepoznate – otvori natječaj_'
            rok = (n.get('rok') or '').split('-')
            rok = '.'.join(reversed(rok)) + '.' if len(rok) == 3 else 'rok nije prepoznat'
            lines.append(f"- **{n['ustanova']}**" + (f" ({n['zupanija']})" if n.get('zupanija') else '') +
                         f"\n  {sp}\n  rok: {rok}" + (' (procjena)' if n.get('rokProcijenjen') and n.get('rok') else '') +
                         f" · [oglas]({n['link']})")
        with open(a.issue, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        log.append(f'{len(new)} novih natječaja za obavijest')
    elif os.path.exists(a.issue):
        os.remove(a.issue)

    print('\n'.join(log))
    print(f'Ukupno u popisu: {len(result)}; novih: {len(new_ids)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
