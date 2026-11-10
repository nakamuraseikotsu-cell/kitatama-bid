"""入札経過（PDF）から、市・工種・金額帯ごとの「入札に参加した会社」を集計する。

公開するのは集計（会社ごとの参加回数・落札回数・平均入札率など）だけで、PDF の本文は公開しない。
出力:
  data/bids/competitors.json  画面で使う集計
  data/bids/report.json       読み取りの状況（件数・読めなかったファイル）
"""
import datetime as dt
import json
import os
import re
import sys
import unicodedata

RAW = 'data/bids/raw'
OUT = 'data/bids/competitors.json'
REPORT = 'data/bids/report.json'
DAYS = 365

LIST_PAGES = {
    '国立市': 'https://www.city.kunitachi.tokyo.jp/machi/nyusatsu/2/14033.html',
    '立川市': 'https://www.city.tachikawa.lg.jp/sangyo/nyusatsu/1003879/index.html',
}

# 工種（件名から判定）。上から順に当てはめる。画面側（index.html の COMP_KINDS）と同じ並び
KINDS = [
    ('下水道', r'下水|管渠|管きょ|汚水|雨水|マンホール'),
    ('水道', r'水道|配水|給水管'),
    ('電気', r'電気|照明|LED|受変電|自家発|発電|電設|放送|防災行政無線|信号|太陽光|キュービクル|非常用'),
    ('空調・給排水', r'空調|冷暖房|給排水|衛生|換気|ガス|ポンプ|ボイラ|管工事|機械設備'),
    ('舗装・道路', r'舗装|道路|街路|路面|歩道|交差点|市道|道の|区画線'),
    ('公園・造園', r'公園|緑地|造園|植栽|樹木|遊具'),
    ('建築', r'建築|改修|改築|新築|増築|屋上|屋根|防水|外壁|内装|トイレ|塗装|解体|体育館|校舎|庁舎|保育園|学童|施設'),
    ('土木', r'改良|築造|護岸|橋|河川|擁壁|土木|整備|水路|排水'),
]
KIND_NAMES = [k for k, _ in KINDS] + ['その他']
BANDS = [(0, 5_000_000), (5_000_000, 20_000_000), (20_000_000, 50_000_000), (50_000_000, 10 ** 13)]

CORP = r'(?:株式会社|有限会社|合同会社|合資会社|合名会社|\(株\)|\(有\)|\(同\)|\(資\)|\(名\))'
CORP_RE = re.compile(CORP)
AMOUNT_RE = re.compile(r'(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d.])')
STATUS = [('win', r'落\s*札|決\s*定'), ('decline', r'辞\s*退'), ('invalid', r'無\s*効|失\s*格|失格'), ('bid', r'参\s*加')]
DATE_RE = re.compile(r'(?:令和|R)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日')


def nfkc(s):
    return unicodedata.normalize('NFKC', s or '')


def kind_of(title):
    t = nfkc(title)
    for name, pat in KINDS:
        if re.search(pat, t):
            return KIND_NAMES.index(name)
    return len(KINDS)


def band_of(price):
    if not price:
        return None
    for i, (lo, hi) in enumerate(BANDS):
        if lo <= price < hi:
            return i
    return len(BANDS) - 1


def wareki(m):
    y = 1 if m.group(1) == '元' else int(m.group(1))
    try:
        return dt.date(2018 + y, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def company_key(name):
    k = nfkc(name)
    k = CORP_RE.sub('', k)
    k = re.sub(r'[\s・･　]', '', k)
    return k


def clean_name(name):
    n = nfkc(name).strip(' |:：、,')
    n = re.sub(r'\s+', '', n)
    n = n.replace('(株)', '株式会社').replace('(有)', '有限会社').replace('(同)', '合同会社').replace('(資)', '合資会社').replace('(名)', '合名会社')
    return n


def amount(s):
    return int(s.replace(',', ''))


def first_amount_after(text, label, window=80):
    m = re.search(label, text)
    if not m:
        return None, ''
    seg = text[m.end():m.end() + window]
    nums = [(a.start(), amount(a.group(1))) for a in AMOUNT_RE.finditer(seg)]
    nums = [(p, n) for p, n in nums if n >= 10000]
    return (nums, seg) if nums else (None, seg)


def parse_price(text):
    """予定価格（税抜）。入札書比較価格があればそれを使う。"""
    nums, seg = first_amount_after(text, r'入\s*札\s*書\s*比\s*較\s*価\s*格')
    if nums:
        return nums[0][1], 'excl'
    nums, seg = first_amount_after(text, r'予\s*定\s*価\s*格')
    if not nums:
        return None, ''
    head = seg[:nums[0][0] + 30]
    if '比較価格' in seg and len(nums) > 1:
        return nums[1][1], 'excl'
    basis = 'excl' if re.search(r'税\s*抜|税\s*別', head) else ('incl' if re.search(r'税\s*込|消費税', head) else '')
    return nums[0][1], basis


def parse_floor(text):
    m = re.search(r'最\s*低\s*制\s*限\s*価\s*格', text)
    if not m:
        return None, False
    seg = text[m.end():m.end() + 80]
    if re.match(r'[\s:：|]*(設定\s*なし|なし|未\s*設定|-|―|算定\s*せず|算定\s*なし)', seg):
        return None, True
    nums = [(a.start(), amount(a.group(1))) for a in AMOUNT_RE.finditer(seg) if amount(a.group(1)) >= 10000]
    if not nums:
        return None, False
    if '比較価格' in seg and len(nums) > 1:
        return nums[1][1], True
    return nums[0][1], True


def parse_bidders(lines):
    out = {}
    for line in lines:
        line = nfkc(line)
        hits = list(CORP_RE.finditer(line))
        if not hits:
            continue
        # 1行に複数社あるとき（2段組の表など）も、社名ごとに区切って読む
        spans = []
        tokens = list(re.finditer(r'\S+', line))
        for h in hits:
            tok = next((t for t in tokens if t.start() <= h.start() < t.end()), None)
            if tok is None:
                continue
            s, e = tok.start(), tok.end()
            word = tok.group(0)
            # 「株式会社 ○○」のように法人格だけが離れている
            if CORP_RE.fullmatch(re.sub(r'[|:：]', '', word)):
                nxt = next((t for t in tokens if t.start() >= e), None)
                prv = next((t for t in reversed(tokens) if t.end() <= s), None)
                def wordlike(t):
                    w = t.group(0) if t is not None else ''
                    return bool(w) and not re.fullmatch(r'[\d,.|円]+|No\.?|№', w) and not any(re.fullmatch(p, w) for _, p in STATUS) and not CORP_RE.search(w)
                if wordlike(prv) and (not spans or prv.start() >= spans[-1][1]):
                    s = prv.start()
                elif wordlike(nxt):
                    e = nxt.end()
            if spans and s < spans[-1][1]:
                continue
            spans.append((s, e))
        for i, (s, e) in enumerate(spans):
            name = clean_name(re.sub(r'^[\d.|]+', '', line[s:e]))
            name = AMOUNT_RE.split(name)[0] if AMOUNT_RE.search(name) else name
            key = company_key(name)
            if len(key) < 2 or re.search(r'市長|市役所|東京都|代表|住所|所在地|件名|業者名', key):
                continue
            rest = line[e:spans[i + 1][0] if i + 1 < len(spans) else len(line)]
            amt = None
            for a in AMOUNT_RE.finditer(rest):
                v = amount(a.group(1))
                if v >= 10000:
                    amt = v
                    break
            status = ''
            for st, pat in STATUS:
                if re.search(pat, rest):
                    status = st
                    break
            if amt is None and status not in ('decline', 'invalid'):
                continue  # 住所の行など
            prev = out.get(key)
            if prev is None or (prev['amount'] is None and amt is not None):
                out[key] = {'name': name, 'amount': amt, 'status': status or ('bid' if amt else '')}
            elif status == 'win':
                prev['status'] = 'win'
    return list(out.values())


def split_cases(text):
    marks = [m.start() for m in re.finditer(r'件\s*名', text)]
    if len(marks) <= 1:
        return [text]
    blocks = []
    starts = marks
    for i, st in enumerate(starts):
        en = starts[i + 1] if i + 1 < len(starts) else len(text)
        blocks.append(text[st:en])
    # 先頭の見出し部分（最初の件名より前）に日付などがあれば最初の案件につける
    head = text[:starts[0]]
    if head.strip():
        blocks[0] = head + '\n' + blocks[0]
    return blocks


def parse_title(block):
    m = re.search(r'件\s*名[\s:：|]*([^\n]*)', block)
    if not m:
        return ''
    t = m.group(1).strip(' |:：')
    if not t:
        nxt = block[m.end():].lstrip('\n').split('\n', 1)[0]
        t = nxt.strip(' |:：')
    return re.sub(r'\s+', '', nfkc(t))[:120]


def parse_case(block, fallback_date=None):
    text = nfkc(block)
    title = parse_title(text)
    dm = None
    for lab in (r'入\s*札\s*(?:月\s*)?日', r'開\s*札\s*日', r'執\s*行\s*日'):
        m = re.search(lab + r'[^\n]{0,20}?' + DATE_RE.pattern, text)
        if m:
            dm = DATE_RE.search(m.group(0))
            break
    if dm is None:
        dm = DATE_RE.search(text)
    date = wareki(dm) if dm else fallback_date
    price, basis = parse_price(text)
    floor, floor_listed = parse_floor(text)
    bidders = parse_bidders(text.split('\n'))
    bids = [b['amount'] for b in bidders if b['amount']]
    # 予定価格が税込で入札額が税抜のとき（落札額が予定価格の約1/1.1以下に偏る）は税抜に直す
    if price and basis == 'incl' and bids and max(bids) <= price / 1.1 * 1.001:
        price = int(price / 1.1)
        floor = int(floor / 1.1) if floor else floor
    return {'title': title, 'date': date.isoformat() if date else '', 'price': price, 'floor': floor,
            'floorListed': floor_listed, 'bidders': bidders,
            'variable': bool(re.search(r'変\s*動\s*型', text))}


def read_pdf(path):
    import pdfplumber  # Actions でだけ使う
    with pdfplumber.open(path) as pdf:
        return '\n'.join((p.extract_text() or '') for p in pdf.pages)


def build(cases_by_city, today):
    since = today - dt.timedelta(days=DAYS)
    companies, cidx = [], {}
    cells = {}
    floor_stats = {}
    period = {}
    used = {}

    def comp(name):
        k = company_key(name)
        if k not in cidx:
            cidx[k] = len(companies)
            companies.append({'name': name, 'n': {}})
        c = companies[cidx[k]]
        c['n'][name] = c['n'].get(name, 0) + 1
        return cidx[k]

    for city, cases in cases_by_city.items():
        for c in cases:
            if not c['date'] or not ('工事' in c['title'] or '修繕' in c['title']):
                continue
            d = dt.date.fromisoformat(c['date'])
            if not (since <= d <= today):
                continue
            bidders = [b for b in c['bidders'] if b['amount'] or b['status'] == 'invalid']
            if not bidders:
                continue
            used[city] = used.get(city, 0) + 1
            p = period.setdefault(city, [c['date'], c['date']])
            p[0], p[1] = min(p[0], c['date']), max(p[1], c['date'])
            kind = kind_of(c['title'])
            band = band_of(c['price'])
            idx = [comp(b['name']) for b in bidders]
            keys = [(kind, band), (kind, 'A'), ('A', band), ('A', 'A')]
            for kk, bb in keys:
                if bb is None:
                    continue
                cell = cells.setdefault(f'{city}|{kk}|{bb}', {'n': 0, 'bidSum': 0, 'firms': {}})
                cell['n'] += 1
                cell['bidSum'] += len(bidders)
                for i, b in zip(idx, bidders):
                    f = cell['firms'].setdefault(i, [0, 0, 0.0, 0])
                    f[0] += 1
                    if b['status'] == 'win':
                        f[1] += 1
                    if c['price'] and b['amount'] and 0.5 < b['amount'] / c['price'] <= 1.0001:
                        f[2] += b['amount'] / c['price']
                        f[3] += 1
            if c['floor'] and c['price'] and 0.5 < c['floor'] / c['price'] <= 1:
                fs = floor_stats.setdefault(city, [])
                fs.append(round(c['floor'] / c['price'] * 100, 2))
            elif c['floorListed'] and not c['floor']:
                floor_stats.setdefault(city + ':none', []).append(1)

    out_cells = {}
    for key, cell in cells.items():
        firms = sorted(cell['firms'].items(), key=lambda x: (-x[1][0], -x[1][1]))
        out_cells[key] = {
            'n': cell['n'],
            'avgBidders': round(cell['bidSum'] / cell['n'], 1),
            'firms': [[i, f[0], f[1], round(f[2] / f[3] * 100, 2) if f[3] else None] for i, f in firms[:40]],
        }
    names = [max(c['n'].items(), key=lambda x: x[1])[0] for c in companies]
    floors = {}
    for city in cases_by_city:
        v = floor_stats.get(city, [])
        floors[city] = {'n': len(v), 'none': len(floor_stats.get(city + ':none', [])),
                        'avg': round(sum(v) / len(v), 2) if v else None,
                        'min': min(v) if v else None, 'max': max(v) if v else None}
    return {
        'updatedAt': today.isoformat(),
        'note': '各市が公表している工事の入札経過をもとに、株式会社誠が会社ごとの参加回数などを集計したものです（本文の転載ではありません）。',
        'kinds': KIND_NAMES,
        'bands': ['500万円未満', '500万〜2,000万円', '2,000万〜5,000万円', '5,000万円以上'],
        'sources': {city: {'url': LIST_PAGES.get(city, ''), 'cases': used.get(city, 0),
                           'from': period.get(city, ['', ''])[0], 'to': period.get(city, ['', ''])[1]}
                    for city in cases_by_city},
        'floors': floors,
        'companies': names,
        'cells': out_cells,
    }


def main():
    today = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    mpath = os.path.join(RAW, 'manifest.json')
    if not os.path.exists(mpath):
        print('PDFがありません')
        return
    with open(mpath, encoding='utf-8') as f:
        manifest = json.load(f)
    by_city, report = {}, {}
    for url, meta in manifest.items():
        rep = report.setdefault(meta['city'], {'pdfs': 0, 'cases': 0, 'withBidders': 0, 'withPrice': 0,
                                               'withDate': 0, 'construction': 0, 'unreadable': [], 'noBidders': []})
        rep['pdfs'] += 1
        try:
            text = read_pdf(os.path.join(RAW, meta['file']))
        except Exception as e:  # noqa: BLE001
            rep['unreadable'].append(url)
            print('読めない', url, e)
            continue
        if len(text.strip()) < 30:
            rep['unreadable'].append(url)
            continue
        fdm = DATE_RE.search(nfkc(meta.get('label', '')))
        fallback = wareki(fdm) if fdm else None
        for block in split_cases(text):
            c = parse_case(block, fallback)
            if not c['title'] and not c['bidders']:
                continue
            rep['cases'] += 1
            rep['withBidders'] += bool(c['bidders'])
            rep['withPrice'] += bool(c['price'])
            rep['withDate'] += bool(c['date'])
            rep['construction'] += ('工事' in c['title'] or '修繕' in c['title'])
            if not c['bidders'] and len(rep['noBidders']) < 15:
                rep['noBidders'].append(url)
            by_city.setdefault(meta['city'], []).append(c)
    data = build(by_city, today)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    for city, rep in report.items():
        rep['used'] = data['sources'].get(city, {}).get('cases', 0)
        rep['companies'] = None
    report['_companies'] = len(data['companies'])
    report['_kinds'] = {}
    for key, cell in data['cells'].items():
        city, k, b = key.split('|')
        if b == 'A' and k != 'A':
            report['_kinds'][f'{city}|{KIND_NAMES[int(k)]}'] = cell['n']
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
