"""国立市・立川市の工事の入札経過（PDF）を集める。

- 一覧ページから PDF のリンクを集め、まだ持っていないものだけを取得する（1.5秒間隔）。
- PDF は data/bids/raw/ に保存する。このフォルダはリポジトリに含めない（.gitignore）。
  GitHub Actions のキャッシュに置き、公開するのは集計結果（build_competitors.py）だけ。
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

RAW = 'data/bids/raw'
MANIFEST = os.path.join(RAW, 'manifest.json')
UA = 'Mozilla/5.0 (compatible; kitatama-bid/1.0; +https://nyusatsu.ma-ko-to-jp.com/)'
SLEEP = 1.5

SOURCES = {
    '国立市': {
        'lists': [
            'https://www.city.kunitachi.tokyo.jp/machi/nyusatsu/2/14033.html',  # 令和8年度
            'https://www.city.kunitachi.tokyo.jp/soshiki/Dept02/Div01/Sec02/gyomu/0215/0216/12576.html',  # 令和7年度
        ],
        'pdf': re.compile(r'(koji|kouji)[^/]*\.pdf$', re.I),
    },
    '立川市': {
        'index': 'https://www.city.tachikawa.lg.jp/sangyo/nyusatsu/1003879/index.html',
        'prefix': '/sangyo/nyusatsu/1003879/',
        'years': re.compile(r'令和\s*[78]\s*年度|2025|2026'),
        'pdf': re.compile(r'kouji[^/]*\.pdf$', re.I),
    },
}

A_RE = re.compile(r'<a\s[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
TAG_RE = re.compile(r'<[^>]+>')


def get(url, binary=False):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    time.sleep(SLEEP)
    if binary:
        return data
    for enc in ('utf-8', 'cp932', 'euc-jp'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode('utf-8', 'replace')


def links(page_url, text):
    out = []
    for href, label in A_RE.findall(text):
        href = html.unescape(href.strip())
        if href.startswith(('mailto:', 'javascript:', '#')):
            continue
        u = urllib.parse.urljoin(page_url, href).split('#')[0]
        out.append((u, re.sub(r'\s+', ' ', html.unescape(TAG_RE.sub('', label))).strip()))
    return out


def kunitachi(src):
    found = {}
    for lst in src['lists']:
        try:
            page = get(lst)
        except Exception as e:  # noqa: BLE001
            print('一覧ページ取得失敗', lst, e)
            continue
        for u, label in links(lst, page):
            if src['pdf'].search(urllib.parse.urlparse(u).path):
                found.setdefault(u, {'list': lst, 'label': label})
    return found


def tachikawa(src):
    found = {}
    try:
        top = get(src['index'])
    except Exception as e:  # noqa: BLE001
        print('立川市の一覧ページ取得失敗', e)
        return found
    years = []
    for u, label in links(src['index'], top):
        p = urllib.parse.urlparse(u).path
        if p.startswith(src['prefix']) and u != src['index'] and src['years'].search(label):
            years.append(u)
    years = list(dict.fromkeys(years))
    print('立川市 年度ページ', len(years), years)
    for yu in years:
        try:
            ypage = get(yu)
        except Exception as e:  # noqa: BLE001
            print('年度ページ取得失敗', yu, e)
            continue
        ybase = urllib.parse.urlparse(yu).path.rsplit('/', 1)[0] + '/'
        days = []
        for u, label in links(yu, ypage):
            p = urllib.parse.urlparse(u).path
            if src['pdf'].search(p):
                found.setdefault(u, {'list': yu, 'label': label})
            elif p.startswith(ybase) and p.endswith('.html') and u != yu:
                days.append((u, label))
        days = list(dict.fromkeys(days))
        print('  ', yu, '入札日ページ', len(days))
        for du, dlabel in days:
            try:
                dpage = get(du)
            except Exception as e:  # noqa: BLE001
                print('入札日ページ取得失敗', du, e)
                continue
            for u, label in links(du, dpage):
                if src['pdf'].search(urllib.parse.urlparse(u).path):
                    found.setdefault(u, {'list': du, 'label': (dlabel + ' ' + label).strip()})
    return found


def main():
    os.makedirs(RAW, exist_ok=True)
    manifest = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding='utf-8') as f:
            manifest = json.load(f)
    only = sys.argv[1:] or list(SOURCES)
    new = 0
    for city in only:
        src = SOURCES[city]
        found = kunitachi(src) if 'lists' in src else tachikawa(src)
        print(city, 'PDF', len(found))
        for u, meta in found.items():
            if u in manifest and os.path.exists(os.path.join(RAW, manifest[u]['file'])):
                continue
            name = hashlib.sha1(u.encode()).hexdigest()[:16] + '.pdf'
            try:
                data = get(u, binary=True)
            except Exception as e:  # noqa: BLE001
                print('PDF取得失敗', u, e)
                continue
            if not data.startswith(b'%PDF'):
                print('PDFではない', u)
                continue
            with open(os.path.join(RAW, name), 'wb') as f:
                f.write(data)
            manifest[u] = {'file': name, 'city': city, 'list': meta['list'], 'label': meta['label'],
                           'sha256': hashlib.sha256(data).hexdigest(), 'fetchedAt': time.strftime('%Y-%m-%d')}
            new += 1
    with open(MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print('新規取得', new, '件／合計', len(manifest), '件')


if __name__ == '__main__':
    main()
