import sys, re, datetime
TODAY=(datetime.datetime.utcnow()+datetime.timedelta(hours=9)).strftime('%Y-%m-%d')
ROWS = ['説明会', '出願期間', '筆記試験', '口述/面接', '合格発表', '過去問']
BAN = ['安全保障', '輸出管理', '出口管理', '成绩开示的申请', '成績開示', '见募集要', '详见', '见专攻HP', '本轮', '巡检', '说明：', '未核对', '<!--', '个人信息']
for p in sys.argv[1:]:
    t = open(p, encoding='utf-8').read(); prob = []
    if t.startswith('---'): prob.append('frontmatter')
    head = t.split('\n## ')[0]
    nl = len(re.findall(r'^- \[', head, re.M))
    if nl > 10: prob.append(f'链接{nl}条')
    m = re.search(r'## ⏰[^\n]*\n(.*?)\n## ', t, re.S)
    if not m: prob.append('无临近提醒')
    else:
        n = len(re.findall(r'^- ', m.group(1), re.M))
        if n > 3: prob.append(f'提醒{n}条')
    m = re.search(r'## 📋[^\n]*\n(.*?)\n## ', t, re.S)
    rows = []
    if m:
        for l in m.group(1).splitlines():
            if l.startswith('|') and not l.startswith('|---') and not l.startswith('| 项目'):
                c = [x.strip() for x in l.strip().strip('|').split('|')]
                rows.append(c[0])
                if len(c) != 3: prob.append(f'日程行{c[0]}列数{len(c)}')
        if not m.group(1).lstrip().startswith('| 项目 | 内容 | 状态 |'): prob.append('日程表头')
    core = [r for r in rows if r != '冬入試']
    if core != ROWS: prob.append('日程行=' + '/'.join(rows))
    for h in ['### 語学', '#### 英語要件', '#### 日本語要件', '### 筆記試験', '### 口述試験 / 面接', '### 出願資格 / 定員', '### 研究室 / 指導教員', '## 📊', '## 📚', '## 🔄']:
        if h not in t: prob.append('缺' + h)
    if not re.search(r'^- \*\*官方形式\*\*：', t, re.M): prob.append('官方形式')
    if not re.search(r'### 研究室 / 指導教員\n- 事前連絡要否：', t): prob.append('事前連絡首条')
    if not re.search(r'### 出願資格 / 定員\n- 募集人员', t): prob.append('募集人员首条')
    lg = re.search(r'## 🔄[^\n]*\n(.*)', t, re.S).group(1).strip() if '## 🔄' in t else ''
    if lg != '- 最新更新：'+TODAY: prob.append('更新日志=' + lg[:60])
    for b in BAN:
        if b in t: prob.append('含「' + b + '」')
    lab = re.search(r'### 研究室 / 指導教員\n(.*?)\n## ', t, re.S)
    if lab:
        L = [l for l in lab.group(1).splitlines() if l.startswith('  - ')]
        rm = len(re.findall(r'researchmap\.jp/', lab.group(1)))
        bad = [l[:40] for l in L if l.count('｜') < 2 and '不募集' not in l]
        prob.append(f'[教员行{len(L)}，rm链接{rm}，缺段{len(bad)}]')
    print(p.split('/')[-1], '；'.join(prob) if prob else 'OK')
