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


# GitHub Contents API: the only remote input is base64-encoded public JSON.
# No remotely retrieved source code is ever executed by this synchronizer.
from unittest.mock import patch
import base64
manifest=json.dumps(original,ensure_ascii=False).encode("utf-8")
payload={"type":"file","encoding":"base64",
         "sha":"example_blob_hash",
         "content":base64.b64encode(manifest).decode("ascii")}
class FakeResponse:
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,max_bytes):return json.dumps(payload).encode("utf-8")
with patch.object(mod.urllib.request,"urlopen",return_value=FakeResponse()) as urlopen:
    got,revision=mod.from_github_api()
    assert got==manifest and revision=="example_blob_hash"
    req=urlopen.call_args.args[0]
    assert "api.github.com" in req.full_url
    assert "config/feeds.json" in req.full_url
payload["content"]="NOT VALID BASE64 !"
with patch.object(mod.urllib.request,"urlopen",return_value=FakeResponse()):
    try:mod.from_github_api()
    except RuntimeError as e:assert "BASE64_INVALID" in str(e)
    else:raise AssertionError("corrupt API content accepted")
print("GITHUB_CONTENTS_API_FORMAT_VALIDATED")
