"""読み取りに失敗したPDFの「形」だけを書き出す（一時的な調査用）。

本文は出さない：見出し語・法人格・記号以外の文字は「字」、数字は「9」に置き換える。
"""
import json, os, re, sys, unicodedata
sys.path.insert(0, os.path.dirname(__file__))
import build_competitors as B

KEEP = ['件名', '件 名', '予定価格', '予 定 価 格', '最低制限価格', '比較価格', '入札書', '落札', '決定', '参加', '辞退', '無効', '失格',
        '株式会社', '有限会社', '(株)', '(有)', '㈱', '㈲', '円', '税', '契約', '金額', '入札', '業者', '番号', '令和', '年', '月', '日',
        '変動型', '設定', 'なし', '工事', '委託', '修繕', '開札', '執行', '経過', '結果', '指名', '一般', '競争', '名称', '商号', '所在地', '代表']
def shape(line):
    out, i = [], 0
    while i < len(line):
        hit = next((k for k in KEEP if line.startswith(k, i)), None)
        if hit:
            out.append(hit); i += len(hit); continue
        c = line[i]
        out.append('9' if c.isdigit() else (c if (c.isspace() or c in ',.|:：()（）/-‐―▲△・、。') else '字'))
        i += 1
    return re.sub('字{4,}', lambda m: '字×%d' % len(m.group(0)), ''.join(out))

rep = json.load(open(B.REPORT, encoding='utf-8'))
man = json.load(open(os.path.join(B.RAW, 'manifest.json'), encoding='utf-8'))
by_url = man
lines = []
for city in ('国立市', '立川市'):
    r = rep.get(city, {})
    picks = list(dict.fromkeys(r.get('noBidders', [])))[:3] + r.get('unreadable', [])[:2]
    for u in picks:
        m = by_url.get(u)
        if not m:
            continue
        p = os.path.join(B.RAW, m['file'])
        try:
            import pdfplumber
            with pdfplumber.open(p) as pdf:
                n = len(pdf.pages)
                t = '\n'.join((pg.extract_text() or '') for pg in pdf.pages[:2])
                imgs = sum(len(pg.images) for pg in pdf.pages[:2])
        except Exception as e:
            lines.append(f'### {city} {u} ERROR {e}'); continue
        lines.append(f'### {city} {os.path.basename(u)} pages={n} chars={len(t)} images={imgs}')
        for ln in unicodedata.normalize('NFKC', t).split('\n')[:70]:
            lines.append(shape(ln))
open('data/bids/debug_shape.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print('\n'.join(lines[:20]))
