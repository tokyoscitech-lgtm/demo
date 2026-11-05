#!/usr/bin/env python3
"""包含学科专项：pick --n 12；apply PLAN.json；status。
计划格式：{claim_token, records:[{id,status:'complete'|'needs_review',tags:[允许类别],evidence_by_field:{类别:依据},sources:[官网URL],notes:说明}]}。
complete须有官网证据、每个标签依据及本年度可接收研究方向核对；待核项目可省略tags，不写分类。
只修改表3/表4包含学科、index同列、分类覆盖规则和独立进度，不改MD与完整巡检日期。
"""
from pathlib import Path
import argparse,csv,io,json,hashlib,os,re,datetime,uuid,contextlib
P=argparse.ArgumentParser(description=__doc__);P.add_argument('command',choices=['pick','apply','status']);P.add_argument('plan',nargs='?');P.add_argument('--root',default=str(Path(__file__).resolve().parents[2]));P.add_argument('--n',type=int,default=12)
STATE='exam-monitor/_scripts/.discipline_review.json'
SHARED=['3_大学研究科専攻一覧.csv','4_考学信息一览.csv','exam-monitor/index.md','exam-monitor/_scripts/field_taxonomy.json']
def sha(b):return hashlib.sha256(b).hexdigest()
def now():return datetime.datetime.now(datetime.timezone.utc)
def load(p,default):return json.loads(p.read_text()) if p.exists() else default
@contextlib.contextmanager
def lock(b):
 import fcntl
 p=b/'exam-monitor/_scripts/.discipline_review.lock'
 with p.open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  try:yield
  finally:fcntl.flock(f,fcntl.LOCK_UN)
def publish(b,changes):
 originals={n:(b/n).read_bytes() if (b/n).exists() else None for n in changes};done=[]
 def atomic(p,data):
  p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.discipline-'+uuid.uuid4().hex+'.tmp')
  try:
   with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
   os.replace(tmp,p)
  finally:
   if tmp.exists():tmp.unlink()
 try:
  for n,data in changes.items():
   assert ((b/n).read_bytes() if (b/n).exists() else None)==originals[n],'Concurrent modification: '+n
   if data!=originals[n]:atomic(b/n,data);done.append(n)
  for n,data in changes.items():assert (b/n).read_bytes()==data,n
 except Exception:
  for n in done:
   if originals[n] is None:(b/n).unlink()
   else:atomic(b/n,originals[n])
  raise
 return done
def csvread(raw):
 f=io.StringIO(raw.decode('utf-8-sig'),newline='');r=csv.DictReader(f);return r.fieldnames,list(r)
def csvbytes(cols,rows):
 f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows);return b'\xef\xbb\xbf'+f.getvalue().encode()
def jsonbytes(o):return (json.dumps(o,ensure_ascii=False,indent=2)+'\n').encode()
def main():
 a=P.parse_args();b=Path(a.root).resolve();sp=b/STATE
 with lock(b):
  state=load(sp,{'version':1,'entries':{},'batch_size':12});assert state['version']==1
  raw={n:(b/n).read_bytes() for n in SHARED};mh,master=csvread(raw[SHARED[0]]);ch,rows=csvread(raw[SHARED[1]])
  assert '包含学科' in mh,'表3缺少包含学科列';excluded=load(b/'exam-monitor/_scripts/program_scope.json',{}).get('excluded',{})
  active=sorted([r for r in master if r['状态'] in ('','再編予定') and r['専攻编号'] not in excluded],key=lambda r:r['専攻编号'])
  entries=state['entries'];pending=[r for r in active if entries.get(r['専攻编号'],{}).get('status')!='complete'];claim=state.get('claim')
  if a.command=='status':
   print(json.dumps({'total':len(active),'complete':len(active)-len(pending),'remaining':len(pending),'needs_review':[i for i,v in entries.items() if v['status']=='needs_review'],'claim':claim},ensure_ascii=False,indent=2));return
  if a.command=='pick':
   assert 1<=a.n<=12,'每轮最多12个'
   if claim and datetime.datetime.fromisoformat(claim['expires_at'])>now():print(json.dumps({'busy':True,'claim':claim},ensure_ascii=False));return
   # Fresh records before failed/uncertain retries, preventing one blocked project from starving the queue.
   pending.sort(key=lambda r:(entries.get(r['専攻编号'],{}).get('status')=='needs_review',r['専攻编号']))
   picked=[]
   for r in pending[:a.n]:
    ident=r['専攻编号'];paths=[p for p in (b/'exam-monitor').rglob(ident+'*.md') if re.match(re.escape(ident)+r'(?![0-9-])',p.name)]
    assert len(paths)==1,(ident,'MD path not unique',paths)
    picked.append({'id':ident,'name':r['大学']+' '+r['研究科']+' '+r['専攻']+' '+r['细分'],'before':r['包含学科'],'md':str(paths[0].relative_to(b)),'md_sha256':sha(paths[0].read_bytes())})
   if not picked:print(json.dumps({'finished':True,'total':len(active)},ensure_ascii=False));return
   claim={'token':uuid.uuid4().hex,'created_at':now().isoformat(),'expires_at':(now()+datetime.timedelta(minutes=55)).isoformat(),'targets':picked,'shared_sha256':{n:sha(v) for n,v in raw.items()}}
   state['claim']=claim;publish(b,{STATE:jsonbytes(state)});print(json.dumps(claim,ensure_ascii=False,indent=2));return
  assert a.plan,'apply需要JSON计划路径';plan=json.loads(Path(a.plan).read_text());assert claim and plan['claim_token']==claim['token'],'claim mismatch'
  assert datetime.datetime.fromisoformat(claim['expires_at'])>now(),'claim expired; re-pick and re-check'
  for n,h in claim['shared_sha256'].items():assert sha(raw[n])==h,'Shared file changed; re-pick/reconcile: '+n
  targets={r['id']:r for r in claim['targets']};records=plan['records'];ids=[r['id'] for r in records];assert len(set(ids))==len(ids) and set(ids)==set(targets),'必须如实记录本轮所有目标（含待核）'
  config=json.loads(raw[SHARED[3]]);allowed=config['allowed'];mm={r['専攻编号']:r for r in master};rm={r['编号']:r for r in rows};changed=[];norm={}
  for r in records:
   i=r['id'];target=targets[i];assert sha((b/target['md']).read_bytes())==target['md_sha256'],'MD changed: '+i
   assert r['status'] in ['complete','needs_review'] and r.get('notes')
   if r['status']=='complete':
    tags=r['tags'];assert tags and len(set(tags))==len(tags) and set(tags)<=set(allowed)
    assert set(r['evidence_by_field'])==set(tags) and all(r['evidence_by_field'].values())
    assert r.get('sources') and all(u.startswith('https://') or u.startswith('http://') for u in r['sources'])
    value=';'.join(x for x in allowed if x in tags);norm[i]=value
    if mm[i]['包含学科']!=value or rm[i]['包含学科']!=value:changed.append(i)
    mm[i]['包含学科']=value;rm[i]['包含学科']=value
    config.setdefault('overrides',{})[i]=value.split(';');config.setdefault('override_reasons',{})[i]=r['notes']+'；证据见.discipline_review.json'
   entries[i]={**r,'before_master':target['before'],'before_csv':next(x['包含学科'] for x in csvread(raw[SHARED[1]])[1] if x['编号']==i),'checked_at':now().isoformat(),'md':target['md'],'md_sha256':target['md_sha256'],'after':norm.get(i,target['before'])}
  # Preserve index cells verbatim except the selected discipline cell; support old numeric first columns.
  index=raw[SHARED[2]].decode();lines=[];found=set()
  for line in index.splitlines(keepends=True):
   cells=line.split('|')
   if len(cells)>=10:
    match=re.search(r'(\d{3}[A-Z]+-\d{2})(?!\d)',cells[7])
    i=match.group(1) if match else ''
    if i in norm:
     old=cells[5];cells[5]=old[:len(old)-len(old.lstrip())]+norm[i]+old[len(old.rstrip()):];line='|'.join(cells);found.add(i)
   lines.append(line)
  assert found==set(norm),'Index missing targets: '+str(set(norm)-found)
  # Prove every other column, row order, and non-target record is unchanged.
  before_m=csvread(raw[SHARED[0]])[1];before_r=csvread(raw[SHARED[1]])[1]
  for before,after in zip(before_m,master):
   assert {k:v for k,v in before.items() if k!='包含学科'}=={k:v for k,v in after.items() if k!='包含学科'}
   if before['専攻编号'] not in norm:assert before==after
  for before,after in zip(before_r,rows):
   assert {k:v for k,v in before.items() if k!='包含学科'}=={k:v for k,v in after.items() if k!='包含学科'}
   if before['编号'] not in norm:assert before==after
  state.pop('claim',None);state['last_batch']={'checked_at':now().isoformat(),'ids':ids,'changed':changed,'complete':len(norm),'needs_review':len(ids)-len(norm)}
  changes={SHARED[0]:csvbytes(mh,master),SHARED[1]:csvbytes(ch,rows),SHARED[2]:''.join(lines).encode(),SHARED[3]:jsonbytes(config),STATE:jsonbytes(state)}
  changed_files=publish(b,changes)
  print(json.dumps({'reviewed':len(records),'complete':len(norm),'needs_review':len(records)-len(norm),'changed_ids':changed,'files':changed_files},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
