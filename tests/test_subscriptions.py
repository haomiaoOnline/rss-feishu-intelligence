#!/usr/bin/env python3
"""Real Feedbro OPML fixtures + dynamic sources + history-preserving offline tests."""
import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest import mock

REPO=pathlib.Path(__file__).resolve().parents[1]
SRC=REPO/'src/rss_feishu_push.py'
spec=importlib.util.spec_from_file_location('unified_rss',SRC)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
sys.path.insert(0,str(REPO/'scripts'))
import subscriptions as subs

all_feeds=json.loads((REPO/'config/feeds.json').read_text())['feeds']
old=[f for f in all_feeds if f.get('type','x_user')=='x_user']
assert len(old)==46
assert len(all_feeds)==66
assert len(mod.normalize_feeds({'feeds':old}))==46
print('PASS_ORIGINAL_46_IDS_STABLE')

candidates=[
    pathlib.Path.home()/'内容生成/Feedbro-AI情报配置/feedbro-ai-intelligence.opml',
    pathlib.Path.home()/'内容生成/Feedbro-AI情报配置/X中文博主RSSHub/46人-已验证-云端RSSHub-本地SSH隧道.opml'
]
with tempfile.TemporaryDirectory(prefix='subscriptions-v2-') as d:
    temp=pathlib.Path(d)
    if candidates[0].is_file():
        imported,skipped=subs.parse_opml(candidates[0])
        assert len(imported)==20 and not skipped
        result=mod.normalize_feeds({'feeds':old+imported})
        assert len(result)==66
        assert {x['handle'].lower() for x in result if x.get('type')!='rss'}=={x['handle'].lower() for x in old}
        print('PASS_REAL_FEEDBRO_20_GENERIC_SOURCES_MERGE_66')
    else:
        imported=[{'type':'rss','name':'Example','group':'Technology','url':'https://example.org/feed.xml'}]
        result=mod.normalize_feeds({'feeds':old+imported})
        print('PASS_GENERIC_RSS_MERGE')
    if candidates[1].is_file():
        importedx,_=subs.parse_opml(candidates[1])
        assert len(importedx)==46
        merged={subs.identity(x):x for x in old}
        merged.update({subs.identity(x):x for x in importedx})
        assert len(merged)==46
        print('PASS_EXISTING_X_OPML_NO_DUPLICATION')

    baseline=mod.normalize_feeds({'feeds':old})
    with mock.patch.object(mod,'fetch',side_effect=lambda f:(f['handle'],[], 'NO_ITEMS')):
        pass
    mod.ROOT=temp
    mod.STATE=temp/'state'/'push.sqlite3'
    db=mod.dbopen()
    before=int(time.time())
    for f in baseline:
        db.execute('INSERT INTO feeds(handle,initialized_at,last_success,last_error) VALUES (?,?,?,?)',
                   (f['handle'],before,before,None))
    db.execute("INSERT INTO articles(id,handle,title,url,published,discovered,delivered) VALUES ('old','dotey','old','https://x.com/dotey/status/1',?,?,1)",
               (before,before))
    db.execute("INSERT INTO batches(created,payload,state,attempts) VALUES (?,'{}','sent',1)",(before,))
    db.commit()
    old_row=db.execute("SELECT * FROM articles WHERE id='old'").fetchone()['id']
    old_batches=db.execute("SELECT count(*) FROM batches WHERE state='sent'").fetchone()[0]
    fake_rss=next(x for x in result if x.get('type')=='rss')
    xml=b'<rss><channel><item><title>First generic post</title><guid>post-1</guid><link>https://example.org/post1</link><pubDate>Wed, 21 Oct 2026 09:00:00 +0000</pubDate></item></channel></rss>'
    class Response:
        status=200
        headers={'Content-Type':'application/rss+xml'}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return xml
    class Opener:
        def open(self,request,timeout=0):return Response()
    with mock.patch.object(mod,'public_dns_only',return_value=None),mock.patch.object(mod.urllib.request,'build_opener',return_value=Opener()):
        handle,posts,error=mod.fetch(fake_rss)
    assert error is None and len(posts)==1
    assert posts[0]['url']=='https://example.org/post1'
    db.execute('INSERT INTO feeds(handle,initialized_at) VALUES (?,?)',(handle,before))
    db.commit()
    assert db.execute("SELECT id FROM articles WHERE id='old'").fetchone()[0]==old_row
    assert db.execute("SELECT count(*) FROM batches WHERE state='sent'").fetchone()[0]==old_batches
    print('PASS_EXISTING_DATABASE_DELIVERY_UNTOUCHED_AND_GENERIC_PARSE')
    h=mod.feed_health(db,result)
    assert h['total']==len(result)
    print('PASS_HEALTH_STATES_SCALABLE')
    db.close()

    for url in ['http://example.org/rss','https://localhost/feed','https://127.0.0.1/rss',
                'https://example.org:8443/rss','https://user:password@example.org/rss',
                'https://169.254.169.254/metadata']:
        try:mod.rss_url(url);raise AssertionError('should reject '+url)
        except ValueError:pass
    print('PASS_NO_INTERNAL_RSS_URLS_AND_CREDENTIAL_URLS')
    for case in [
        {'feeds':[]},
        {'feeds':old+[old[0]]},
        {'feeds':old+[{'type':'rss','name':'bad','group':'x','url':'http://example.org/feed'}]}
    ]:
        try:mod.normalize_feeds(case);raise AssertionError('should reject')
        except ValueError:pass
    print('PASS_INVALID_FEED_INPUT_FAIL_CLOSED')
    data=mod.normalize_feeds({'feeds':old})
    encoded=subs.to_opml(data,'http://127.0.0.1:1200')
    f=temp/'roundtrip.opml';f.write_bytes(encoded)
    parsed,_=subs.parse_opml(f)
    assert len(parsed)==len(data)
    print('PASS_OPML_EXPORT_ROUNDTRIP_46')

print('SUBSCRIPTIONS_V2_OFFLINE_TESTS_OK')
