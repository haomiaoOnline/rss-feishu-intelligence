import importlib.util,json,sys,tempfile,pathlib,time,datetime
from unittest.mock import patch
program=pathlib.Path(__file__).resolve().parents[1]/'src/rss_feishu_push.py'
spec=importlib.util.spec_from_file_location('rsspush',program)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory(prefix='rsspush-unit-') as root:
 p=pathlib.Path(root);mod.ROOT=p;mod.FEEDS_FILE=p/'feeds.json';mod.STATE=p/'state/db.sqlite'
 real=program.parents[1]/'config/feeds.json'
 source=json.loads(real.read_text(encoding='utf-8'))['feeds']
 canonical={'feeds':[f for f in source if f.get('type','x_user')=='x_user']}
 mod.FEEDS_FILE.write_text(json.dumps(canonical,ensure_ascii=False),encoding='utf-8')
 feeds=mod.loadfeeds()
 db=mod.dbopen()
 now=int(time.time())
 counts=[19]*21+[18]*25;assert sum(counts)==849
 def fake(feed):
  idx=feeds.index(feed)
  items=[{'id':f'{feed["handle"]}-{j}','handle':feed['handle'],'title':f'历史文章{j}','url':f'https://x.com/{feed["handle"]}/status/{1000+j}','published':now-3600*j} for j in range(counts[idx])]
  return feed['handle'],items,None
 mod.fetch=fake
 ok,new,bad=mod.poll(db,feeds)
 assert (ok,new,len(bad))==(46,0,0)
 assert db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==849
 assert db.execute('SELECT COUNT(*) FROM articles WHERE delivered=0').fetchone()[0]==0
 print('PASS_BOOTSTRAP_849_SUPPRESSED')
 newids={'dotey','isituking','gefei55'}
 def newer(feed):
  items=fake(feed)[1]
  if feed['handle'] in newids:
   items=items+[{'id':feed['handle']+'-new','handle':feed['handle'],'title':'最新内容','url':f'https://x.com/{feed["handle"]}/status/99999999','published':now+1}]
  return feed['handle'],items,None
 mod.fetch=newer
 ok,new,bad=mod.poll(db,feeds)
 assert new==3
 b=mod.make_batch(db,feeds,now+1)
 assert b is not None
 assert json.loads(b['payload'])['msg_type']=='interactive'
 assert json.loads(b['payload'])['card']['header']['title']['content'].startswith('RSS情报')
 assert 3 <= len(json.loads(b['payload'])['card']['elements']) <= 12
 assert json.loads(b['payload'])['card']['elements'][1]['tag']=='div'
 assert json.loads(b['payload'])['card']['elements'][1]['text']['content'].count('https://x.com/')==3
 attempts=[]
 def reject(payload):attempts.append('reject');raise mod.KnownFailure('PROVIDER_CODE_19001')
 mod.send_payload=reject;mod.deliver(db,b,now+1)
 assert db.execute('SELECT state FROM batches WHERE id=?',(b['id'],)).fetchone()[0]=='retry'
 db.execute('UPDATE batches SET next_try=0 WHERE id=?',(b['id'],));db.commit()
 def success(payload):attempts.append('ok');return True
 mod.send_payload=success
 b=db.execute('SELECT * FROM batches WHERE id=?',(b['id'],)).fetchone()
 mod.deliver(db,b,now+3600)
 assert db.execute('SELECT COUNT(*) FROM articles WHERE delivered=0').fetchone()[0]==0
 assert db.execute('SELECT state FROM batches WHERE id=?',(b['id'],)).fetchone()[0]=='sent'
 print('PASS_FAILURE_RETRY_AND_MARK_SENT')
 ok,new,bad=mod.poll(db,feeds)
 assert new==0
 assert mod.make_batch(db,feeds,now+7200) is None
 print('PASS_NO_DUPLICATES_ON_REPEAT')
 assert all(x=='reject' or x=='ok' for x in attempts)
 db.close()
print('UNIT_TEST_SUITE_OK')
