# 取得した東京都工事設計単価表（Excel）を、画面で検索できるJSONにまとめる。
# 出典：東京都財務局「東京都工事設計単価表」（東京都オープンデータカタログ、CC BY）
import json, os, re, unicodedata, glob
import openpyxl

HERE = os.path.dirname(__file__)
RAW = os.path.join(HERE, '..', 'data', 'unit_prices', 'raw')
OUT = os.path.join(HERE, '..', 'data', 'unit_prices', 'unit_prices.json')
SKIP_SHEET = re.compile(r'島|父島|母島|電波障害')   # 伊豆・小笠原の港湾資材、電波障害調査は北多摩の工事では使わないので除く

def norm(s):
    s = unicodedata.normalize('NFKC', str(s if s is not None else ''))
    s = s.replace('\n', ' ')
    return re.sub(r'\s+', ' ', s).strip()

def key(s):
    return re.sub(r'\s+', '', norm(s))

def category(sheet_title):
    t = norm(sheet_title)
    t = re.sub(r'^R\s*\d+(\.\d+)?_?', '', t).strip()
    if t == '港湾資材':
        t = '港湾資材(東京港)'
    return t

def price_of(v):
    if isinstance(v, (int, float)):
        return int(round(v)) if float(v).is_integer() else float(v)
    s = norm(v)
    if s in ('', '―', '-', '－', 'ー'):
        return None
    s2 = s.replace(',', '')
    try:
        f = float(s2)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None

def parse_material_sheet(ws):
    rows = list(ws.iter_rows(values_only=True))
    hdr = None
    for i, r in enumerate(rows[:10]):
        cells = [key(c) for c in r]
        if '品名' in cells and '単価' in cells:
            hdr = (i, {name: cells.index(name) for name in ('品名', '規格', '単位', '単価', '備考') if name in cells})
            break
    if not hdr:
        return []
    start, col = hdr
    out = []
    for r in rows[start + 1:]:
        get = lambda k: r[col[k]] if k in col and col[k] < len(r) else None
        name = norm(get('品名'))
        if not name or key(name) == '品名':
            continue
        price_raw = get('単価')
        out.append({
            'name': name, 'spec': norm(get('規格')), 'unit': norm(get('単位')),
            'price': price_of(price_raw), 'priceText': '' if price_of(price_raw) is not None else norm(price_raw),
            'note': norm(get('備考'))
        })
    return out

def parse_labor_sheet(ws):
    # 2列ずつ（職種・金額）並んだ表。見出し行で区分を切り替える。
    out, cat = [], '公共工事設計労務単価'
    for r in ws.iter_rows(values_only=True):
        cells = [c for c in r if c is not None and norm(c) != '']
        if not cells:
            continue
        first = norm(cells[0])
        if '設計労務単価' in first and len(cells) == 1:
            cat = '公共工事設計労務単価'; continue
        if '技術者単価' in first and len(cells) == 1:
            cat = '設計業務委託等技術者単価'; continue
        if re.match(r'^[①②③④⑤]', first):
            continue
        for j in range(0, len(cells) - 1, 2):
            n, v = norm(cells[j]), cells[j + 1]
            if not n or key(n) in ('職種', '技術者の職種') or n.startswith('※'):
                continue
            p = price_of(v)
            out.append({'name': n.rstrip('※'), 'spec': '東京地区' if cat == '公共工事設計労務単価' else '基準日額', 'unit': '日',
                        'price': p, 'priceText': '' if p is not None else norm(v), 'note': '', 'cat': cat})
    return out

LABOR_PDFS = []   # (ファイル, 版の日付, 版の名前, 公表URL)。過去の労務単価のPDFを入手できたら足す

def parse_labor_pdf(path):
    try:
        import pdfplumber
    except ImportError:
        print('pdfplumber がないため、過去の労務単価は読みません'); return []
    text = ''
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or '') + '\n'
    text = text.split('設計業務委託等技術者単価')[0]   # 労務単価の表だけ
    out = []
    for line in text.splitlines():
        for m in re.finditer(r'([^\s\d,※－\-]+?)※?\s+([\d,]+|－|-)(?=\s|$)', norm(line)):
            name = m.group(1).strip()
            if key(name) in ('職種', '東京地区') or '単価' in name:
                continue
            p = price_of(m.group(2))
            out.append({'name': name, 'spec': '東京地区', 'unit': '日', 'price': p, 'priceText': '' if p is not None else '-', 'note': '', 'cat': '公共工事設計労務単価'})
    return out

def main():
    manifest = json.load(open(os.path.join(RAW, 'manifest.json'), encoding='utf-8'))
    versions = sorted(manifest.values(), key=lambda m: m['issued'])
    for fn, issued, name, url in LABOR_PDFS:
        if os.path.exists(os.path.join(RAW, fn)):
            versions.append({'issued': issued, 'name': name, 'url': url, 'pdf': fn})
    versions.sort(key=lambda m: m['issued'])
    items = {}   # key -> item
    vlist = []
    for vi, m in enumerate(versions):
        if m.get('pdf'):
            vlist.append({'issued': m['issued'], 'name': m['name'], 'url': m['url']})
            for r in parse_labor_pdf(os.path.join(RAW, m['pdf'])):
                k = '|'.join([r['cat'], key(r['name']), key(r['spec']), key(r['unit'])])
                it = items.setdefault(k, {'c': r['cat'], 'n': r['name'], 's': r['spec'], 'u': r['unit'], 'h': [], 'b': ''})
                if not it['h'] or it['h'][-1][0] != vi:
                    it['h'].append([vi, r['price'] if r['price'] is not None else r['priceText']])
            continue
        vlist.append({'issued': m['issued'], 'name': re.sub(r'^【公表用】|\.xlsx$', '', m['name']), 'url': m['url']})
        wb = openpyxl.load_workbook(os.path.join(RAW, m['issued'] + '.xlsx'), read_only=True, data_only=True)
        for ws in wb.worksheets:
            if SKIP_SHEET.search(ws.title):
                continue
            if '労務' in ws.title:
                rows = parse_labor_sheet(ws)
            else:
                cat = category(ws.title)
                rows = [dict(r, cat=cat) for r in parse_material_sheet(ws)]
            for r in rows:
                k = '|'.join([r['cat'], key(r['name']), key(r['spec']), key(r['unit'])])
                it = items.get(k)
                if not it:
                    it = items[k] = {'c': r['cat'], 'n': r['name'], 's': r['spec'], 'u': r['unit'], 'h': [], 'b': r['note']}
                hist = it['h']
                val = r['price'] if r['price'] is not None else (r['priceText'] or None)
                # 同じ版で同じ品目が2回出たら、最初の値を使う
                if hist and hist[-1][0] == vi:
                    continue
                hist.append([vi, val])
                if r['note']:
                    it['b'] = r['note']
    cats = sorted({it['c'] for it in items.values()})
    data = {
        'source': '東京都財務局「東京都工事設計単価表」（東京都オープンデータカタログ、CC BY）を加工して作成',
        'sourceUrl': 'https://www.zaimu.metro.tokyo.lg.jp/kenchiku/eizen/eizen-tanka',
        'note': '市販刊行物（建設物価・積算資料など）に載っている資材の価格は、都の単価表には載っていません。伊豆・小笠原の港湾資材と電波障害調査は除いています。',
        'versions': vlist,
        'categories': cats,
        'items': [[cats.index(it['c']), it['n'], it['s'], it['u'], it['h'], it['b']] for it in items.values()]
    }
    json.dump(data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('items', len(data['items']), 'versions', [v['issued'] for v in vlist], 'cats', cats, 'bytes', os.path.getsize(OUT))

if __name__ == '__main__':
    main()
