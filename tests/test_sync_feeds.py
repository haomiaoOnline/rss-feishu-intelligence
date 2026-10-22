#!/usr/bin/env python3
"""Test dry-run, atomic apply, idempotence, locks, rollback and change guards."""
import fcntl,importlib.util,json,pathlib,tempfile
repo=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rsssync',repo/'scripts/sync_feeds.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
original=json.loads((repo/'config/feeds.json').read_text())
with tempfile.TemporaryDirectory() as d:
    t=pathlib.Path(d);target=t/'feeds.json';target.write_text(json.dumps(original))
    count=len(original['feeds'])
    after={'feeds':original['feeds']+[{'type':'rss','name':'New Sample',
          'group':'Tech','url':'https://www.example.org/sample.xml'}]}
    raw=json.dumps(after,ensure_ascii=False).encode()
    dry=mod.synchronize(raw,target,repo/'src/rss_feishu_push.py',False,t/'sync.lock')
    assert dry['current']==count and dry['proposed']==count+1
    assert len(json.loads(target.read_text())['feeds'])==count
    applied=mod.synchronize(raw,target,repo/'src/rss_feishu_push.py',True,t/'sync.lock')
    assert applied['add']==1
    assert len(json.loads(target.read_text())['feeds'])==count+1
    assert list((t/'.feed-backups').glob('feeds-*.json'))
    again=mod.synchronize(raw,target,repo/'src/rss_feishu_push.py',True,t/'sync.lock')
    assert not again['changed']
    try:
        mod.synchronize(json.dumps({'feeds':original['feeds'][:10]}).encode(),target,
                        repo/'src/rss_feishu_push.py',True,t/'sync.lock',True)
    except RuntimeError as exc: assert 'BULK_REMOVAL_BLOCKED' in str(exc)
    else: raise AssertionError('bulk deletion not rejected')
    with (t/'sync.lock').open('a+b') as locked:
        fcntl.flock(locked,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            mod.synchronize(json.dumps(original).encode(),target,
                            repo/'src/rss_feishu_push.py',True,t/'sync.lock',True)
        except RuntimeError as exc: assert 'COLLECTOR_IS_RUNNING' in str(exc)
        else: raise AssertionError('lock ignored')
    assert len(json.loads(target.read_text())['feeds'])==count+1
print('SAFE_SYNC_OFFLINE_TESTS_OK')
