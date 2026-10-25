#!/usr/bin/env python3
"""按表4「更新日期」重跑旧条目的辅助脚本（2026-10-24 定时任务用）。

在 demo 根目录运行：
  python3 exam-monitor/_scripts/recheck_by_date.py pick [--n 6] [--before 2026-10-20] [--claim]
      选出表4中更新日期早于 --before 的条目，按编号顺序取前 n 个研究科（编号前4位；
      _scripts/.recheck_priority.json 的 {"prefixes": ["012", ...]} 里的前缀优先），
      跳过 3 小时内已被其他轮次认领的研究科；--claim 时登记认领。输出 JSON。
  python3 exam-monitor/_scripts/recheck_by_date.py preview <md相对exam-monitor路径...>
      不写表，按当前 md 解析出表4各列（忽略日期核对缓存），并和表4现值对照。
  python3 exam-monitor/_scripts/recheck_by_date.py review <编号> 英語|日本語|事前連絡 "<值>" "<理由>"
      把核对后的值写进对应 .reviewed_*.json（带当前 md 的哈希）。
  python3 exam-monitor/_scripts/recheck_by_date.py dates <编号> "<春夏出願>" "<春夏試験>" "<秋冬出願>" "<秋冬試験>"
      把核对后的四个日程列写进 .reviewed_dates.json（带当前 md 的哈希）。
  python3 exam-monitor/_scripts/recheck_by_date.py stale <编号...>
      列出这些编号在各 .reviewed_*.json 里哈希已过期的条目（md 改过后需要复核）。
  python3 exam-monitor/_scripts/recheck_by_date.py release <研究科编号...>
      完成后释放认领。
"""
import csv, glob, hashlib, importlib.util, json, os, re, sys, time, unicodedata

ROOT = os.getcwd()
EM = os.path.join(ROOT, 'exam-monitor')
S = os.path.join(EM, '_scripts')
CSV = os.path.join(ROOT, '4_考学信息一览.csv')
CLAIMS = os.path.join(S, '.recheck_claims.json')
FILES = {'英語': '.reviewed_eng.json', '日本語': '.reviewed_jp.json', '事前連絡': '.reviewed_jizen.json'}
DATE_COLS = ('春夏_出願时间', '春夏_試験时间', '秋冬_出願时间', '秋冬_試験时间')
N = lambda s: unicodedata.normalize('NFC', s)


def rows():
    return list(csv.DictReader(open(CSV, encoding='utf-8-sig')))


def md_paths():
    out = {}
    for p in glob.glob(os.path.join(EM, '[0-9]*', '**', '*.md'), recursive=True):
        b = N(os.path.basename(p))
        m = re.match(r'\d{3}[A-Z]-\d{2}(?:-\d+)?', b)
        if m and '_冬入試' not in b:
            out.setdefault(m.group(), os.path.relpath(p, EM))
    return out


def sha(rel):
    return hashlib.sha256(open(os.path.join(EM, rel), encoding='utf-8').read().encode('utf-8')).hexdigest()


def load(f):
    p = os.path.join(S, f)
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return {'version': 1, 'entries': {}}


def save(f, d):
    p = os.path.join(S, f)
    tmp = p + '.tmp'
    open(tmp, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2 if 'dates' in f else 1))
    os.replace(tmp, p)


def cmd_pick(a):
    n = int(a.get('--n', 6)); before = a.get('--before', '2026-10-20')
    claims = load(os.path.basename(CLAIMS)).get('claims', {}) if os.path.exists(CLAIMS) else {}
    now = time.time()
    claims = {k: v for k, v in claims.items() if now - v < 3 * 3600}
    paths = md_paths()
    groups = {}
    for r in sorted(rows(), key=lambda r: r['编号']):
        if (r['更新日期'] or '0000') >= before:
            continue
        g = r['编号'][:4]
        groups.setdefault(g, []).append(r)
    total = len(groups)
    # 优先清单（.recheck_priority.json 的 prefixes，按大学编号或研究科编号前缀匹配）排在前面
    try:
        pri = json.load(open(os.path.join(S, '.recheck_priority.json'), encoding='utf-8')).get('prefixes', [])
    except Exception:
        pri = []
    def rank(g):
        for i, pf in enumerate(pri):
            if g.startswith(pf):
                return i
        return len(pri)
    picked = []
    for g, rs in sorted(groups.items(), key=lambda kv: (rank(kv[0]), kv[0])):
        if g in claims:
            continue
        picked.append({'研究科编号': g, '大学': rs[0]['大学'], '研究科': rs[0]['研究科'],
                       '専攻': [{'编号': r['编号'], '専攻': r['専攻'], '更新日期': r['更新日期'], 'md': paths.get(r['编号'], '')} for r in rs]})
        if len(picked) >= n:
            break
    if '--claim' in a:
        for p in picked:
            claims[p['研究科编号']] = now
        save(os.path.basename(CLAIMS), {'version': 1, 'claims': claims})
    print(json.dumps({'剩余研究科': total, '本轮': picked}, ensure_ascii=False, indent=1))


def cmd_release(args):
    d = load(os.path.basename(CLAIMS)); c = d.get('claims', {})
    for g in args:
        c.pop(g, None)
    save(os.path.basename(CLAIMS), {'version': 1, 'claims': c})
    print('released', args)


def cmd_preview(args):
    spec = importlib.util.spec_from_file_location('ec', os.path.join(S, 'exam_csv.py'))
    ec = importlib.util.module_from_spec(spec); spec.loader.exec_module(ec)
    ec.AUDIT_OFF = True
    idx = ec.load_index(EM)
    old = {r['编号']: r for r in rows()}
    cols = list(DATE_COLS) + ['英語', '日本語', '事前連絡', '定員/倍率']
    for rel in args:
        rel = N(rel)
        r = ec.row_for(EM, rel, idx)
        k = r.get('编号', os.path.basename(rel)[:7])
        print('##', k, rel)
        for c in cols:
            v = r.get(c, ''); o = old.get(k, {}).get(c, '∅')
            print(f'  {c}: {v}' + ('' if v == o else f'   ⟵表4现值: {o}'))


def cmd_review(args):
    k, col, val, why = args[0], args[1], args[2], (args[3] if len(args) > 3 else '')
    f = FILES[col]; d = load(f); rel = md_paths()[k]
    d.setdefault('entries', {})[k] = {'source_sha256': sha(rel), col: val, '理由': why}
    save(f, d); print('ok', f, k, col, val)


def cmd_dates(args):
    k = args[0]; vals = args[1:5]
    assert len(vals) == 4, '需要四个日程值（可为空字符串）'
    d = load('.reviewed_dates.json'); rel = md_paths()[k]
    d.setdefault('entries', {})[k] = {'source_sha256': sha(rel), 'columns': dict(zip(DATE_COLS, vals))}
    save('.reviewed_dates.json', d); print('ok dates', k, vals)


def cmd_stale(args):
    paths = md_paths()
    for f in list(FILES.values()) + ['.reviewed_dates.json']:
        d = load(f).get('entries', {})
        for k in args:
            e = d.get(k)
            if e and k in paths and e.get('source_sha256') != sha(paths[k]):
                print('STALE', f, k, json.dumps({x: y for x, y in e.items() if x not in ('source_sha256',)}, ensure_ascii=False)[:300])


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    c, rest = sys.argv[1], sys.argv[2:]
    if c == 'pick':
        a = {}
        it = iter(rest)
        for x in it:
            if x in ('--n', '--before'):
                a[x] = next(it)
            else:
                a[x] = True
        cmd_pick(a)
    elif c == 'preview': cmd_preview(rest)
    elif c == 'review': cmd_review(rest)
    elif c == 'dates': cmd_dates(rest)
    elif c == 'stale': cmd_stale(rest)
    elif c == 'release': cmd_release(rest)
    else: print(__doc__)


if __name__ == '__main__':
    main()
