# 東京都工事設計単価表（東京都財務局・オープンデータ CC BY）のExcel版を取得して保存する。
# 出典：東京都財務局「東京都工事設計単価表について」https://www.zaimu.metro.tokyo.lg.jp/kenchiku/eizen/eizen-tanka
import hashlib, json, os, re, sys, urllib.parse, urllib.request, datetime

PAGE = 'https://www.zaimu.metro.tokyo.lg.jp/kenchiku/eizen/eizen-tanka'
BASE = 'https://www.zaimu.metro.tokyo.lg.jp'
OUT = os.path.join(os.path.dirname(__file__), '..', 'data', 'unit_prices', 'raw')
UA = 'kitatama-bid/1.0 (+https://nyusatsu.ma-ko-to-jp.com/)'
# 公開ページから消えた過去の版も、URLが分かっていれば取り直せるように並べておく
EXTRA = [u for u in os.environ.get('EXTRA_URLS', '').split() if u]

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers

def zen2han(s):
    return s.translate(str.maketrans('０１２３４５６７８９', '0123456789'))

def issued_from_name(name):
    m = re.search(r'令和(\d+)年(\d+)月(\d+)日', zen2han(name))
    if not m:
        return ''
    y, mo, d = 2018 + int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f'{y:04d}-{mo:02d}-{d:02d}'

def main():
    os.makedirs(OUT, exist_ok=True)
    html, _ = get(PAGE)
    html = html.decode('utf-8', 'replace')
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
    urls = [urllib.parse.urljoin(BASE, h) for h, t in links if 'エクセル' in re.sub('<[^>]+>', '', t)]
    urls += EXTRA
    manifest_path = os.path.join(OUT, 'manifest.json')
    manifest = json.load(open(manifest_path, encoding='utf-8')) if os.path.exists(manifest_path) else {}
    for url in dict.fromkeys(urls):
        body, headers = get(url)
        cd = headers.get('Content-Disposition', '')
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
        name = urllib.parse.unquote(m.group(1)) if m else os.path.basename(url)
        issued = issued_from_name(name)
        if not issued or not name.lower().endswith(('.xlsx', '.xls')):
            print('skip', url, name); continue
        fn = f'{issued}.xlsx'
        sha = hashlib.sha256(body).hexdigest()
        if manifest.get(fn, {}).get('sha256') == sha:
            print('same', fn); continue
        open(os.path.join(OUT, fn), 'wb').write(body)
        manifest[fn] = {'url': url, 'name': name, 'issued': issued, 'sha256': sha, 'bytes': len(body),
                        'fetchedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}
        print('saved', fn, len(body), name)
    json.dump(manifest, open(manifest_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)

# Excel版のない過去の労務単価（昨年度との比較用）。財務局の公表PDF。
PDF_EXTRA = {
    'labor-2025-03.pdf': 'https://www.zaimu.metro.tokyo.lg.jp/documents/d/zaimu/7roumur7',   # 公共工事設計労務単価〔令和7年3月〕
}

def fetch_pdfs():
    for fn, url in PDF_EXTRA.items():
        path = os.path.join(OUT, fn)
        if os.path.exists(path):
            continue
        try:
            body, _ = get(url)
        except Exception as e:
            print('pdf fail', url, e); continue
        if body[:4] == b'%PDF':
            open(path, 'wb').write(body); print('saved', fn, len(body))

if __name__ == '__main__':
    main()
    fetch_pdfs()
