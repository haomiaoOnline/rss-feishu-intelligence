#!/usr/bin/env python3
"""Synchronize only the committed Git feed manifest. Never touch SQLite.

The service's systemd ExecStart and this synchronizer share an exclusive flock.
Git is a public trusted source of configuration, never of executed code.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error

DEFAULT_REPO=Path('/opt/rss-feishu-intelligence-source')
DEFAULT_APP=Path('/opt/rss-feishu-push/push.py')
DEFAULT_TARGET=Path('/opt/rss-feishu-push/feeds.json')
EXPECTED_REMOTE='https://github.com/haomiaoOnline/rss-feishu-intelligence.git'
LOCK=Path('/run/rss-feishu-push.lock')

def command(args):
    p=subprocess.run(args,capture_output=True,timeout=35,check=False)
    if p.returncode:
        raise RuntimeError('SOURCE_GIT_COMMAND_FAILED')
    return p.stdout

def from_git(repo,fetch):
    source=command(['git','-C',str(repo),'remote','get-url','origin']).decode().strip()
    if source not in (EXPECTED_REMOTE,EXPECTED_REMOTE[:-4]):
        raise ValueError('UNTRUSTED_REMOTE_ORIGIN')
    if fetch:
        command(['git','-C',str(repo),'fetch','--quiet','--depth=1','origin','main'])
    data=command(['git','-C',str(repo),'show','refs/remotes/origin/main:config/feeds.json'])
    commit=command(['git','-C',str(repo),'rev-parse','refs/remotes/origin/main']).decode().strip()
    return data,commit

def from_github_api():
    # Read exactly one public file via GitHub REST; raw.githubusercontent.com
    # and full git-clone are not reachable from some mainland VPS networks.
    url=('https://api.github.com/repos/haomiaoOnline/'
         'rss-feishu-intelligence/contents/config/feeds.json?ref=main')
    req=urllib.request.Request(url,headers={
        'Accept':'application/vnd.github+json',
        'User-Agent':'rss-feishu-subscription-sync/2.0'})
    with urllib.request.urlopen(req,timeout=22) as response:
        if response.status!=200:
            raise RuntimeError('GITHUB_API_HTTP_ERROR')
        raw=response.read(250000)
    obj=json.loads(raw)
    if obj.get('type')!='file' or obj.get('encoding')!='base64':
        raise RuntimeError('GITHUB_API_NOT_INLINE_BASE64_FILE')
    encoded=obj.get('content')
    if not isinstance(encoded,str) or len(encoded)>200000:
        raise RuntimeError('GITHUB_API_CONTENT_SIZE_INVALID')
    try:
        data=base64.b64decode(''.join(encoded.split()),validate=True)
    except (ValueError,base64.binascii.Error) as exc:
        raise RuntimeError('GITHUB_API_BASE64_INVALID') from exc
    if len(data)>100000 or len(data)<20:
        raise RuntimeError('GITHUB_CONFIG_SIZE_OUT_OF_BOUNDS')
    return data,obj.get('sha','unknown')

def read_module(script):
    spec=importlib.util.spec_from_file_location('rss_feishu_push_sync_validate',script)
    if not spec or not spec.loader:
        raise RuntimeError('APP_NOT_INSTALLED')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module,'normalize_feeds') or not hasattr(module,'rss_url'):
        raise RuntimeError('COLLECTOR_VERSION_TOO_OLD_FOR_UNIFIED_SUBSCRIPTIONS')
    return module

def synchronize(payload, target, app, apply, lock_file, allow_removals=False):
    module=read_module(app)
    candidate=module.normalize_feeds(json.loads(payload))
    old=module.normalize_feeds(json.loads(target.read_bytes())) if target.exists() else []
    def identity(x):
        return 'rss:'+x['url'] if x.get('type')=='rss' else 'x:'+x['handle'].lower()
    before={identity(x) for x in old}
    after={identity(x) for x in candidate}
    removed=before-after;added=after-before
    # An accidental mass overwrite must be reviewed even for trusted Git main.
    if removed and not allow_removals:
        raise RuntimeError('REMOVALS_NEED_EXPLICIT_PERMISSION')
    if len(removed)>max(5,len(old)//5):
        raise RuntimeError('BULK_REMOVAL_BLOCKED')
    if len(added)>max(25,len(old)//2):
        raise RuntimeError('BULK_ADDITION_BLOCKED')
    output={'current':len(old),'proposed':len(candidate),
            'add':len(added),'remove':len(removed),'changed':old!=candidate,
            'dry_run':not apply}
    print(json.dumps(output,ensure_ascii=False))
    if not apply or not output['changed']:
        return output
    lock_file.parent.mkdir(parents=True,exist_ok=True)
    with lock_file.open('a+b') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise RuntimeError('COLLECTOR_IS_RUNNING_RETRY_LATER') from exc
        # Guard against config changes during lock acquisition.
        current=target.read_bytes() if target.exists() else b''
        original={identity(x) for x in module.normalize_feeds(json.loads(current))} if current else set()
        if original!=before:
            raise RuntimeError('FEED_CHANGED_SINCE_PLAN_RETRY')
        target.parent.mkdir(parents=True,exist_ok=True)
        if current:
            backup=target.parent/'.feed-backups'
            backup.mkdir(exist_ok=True,mode=0o700)
            label=hashlib.sha256(current).hexdigest()[:16]
            dst=backup/('feeds-'+label+'.json')
            if not dst.exists():
                fd=os.open(dst,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'wb') as out:out.write(current)
        # Derived runtime handles are never persisted for generic RSS.
        canonical=[({k:v for k,v in row.items() if k!='handle'} if row.get('type')=='rss'
                    else row) for row in candidate]
        normalized=(json.dumps({'feeds':canonical},ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        fd,tmp=tempfile.mkstemp(prefix='.feeds-',dir=target.parent)
        try:
            with os.fdopen(fd,'wb') as out:
                out.write(normalized);out.flush();os.fsync(out.fileno())
            os.chmod(tmp,0o640)
            os.replace(tmp,target)
            directory=os.open(target.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        print('SAFE_ATOMIC_FEED_UPDATE_APPLIED')
    return output

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--repo',type=Path,default=DEFAULT_REPO)
    p.add_argument('--source',type=Path)
    p.add_argument('--target',type=Path,default=DEFAULT_TARGET)
    p.add_argument('--app',type=Path,default=DEFAULT_APP)
    p.add_argument('--lock',type=Path,default=LOCK)
    p.add_argument('--fetch',action='store_true')
    p.add_argument('--api',action='store_true')
    p.add_argument('--allow-removals',action='store_true')
    p.add_argument('--apply',action='store_true')
    args=p.parse_args(argv)
    if args.api and (args.source or args.fetch):raise ValueError('API_CANNOT_COMBINE_SOURCE_OR_FETCH')
    if args.api:
        data,origin=from_github_api()
    elif args.source:
        if args.fetch:raise ValueError('FETCH_NOT_COMPATIBLE_WITH_SOURCE')
        data=args.source.read_bytes()
        origin='local-file'
    else:
        data,origin=from_git(args.repo,args.fetch)
    report=synchronize(data,args.target,args.app,args.apply,args.lock,args.allow_removals)
    print('SOURCE_REVISION',origin[:16] if origin!='local-file' else origin)
    return report

if __name__=='__main__':
    try: main()
    except (RuntimeError,ValueError,FileNotFoundError,json.JSONDecodeError) as e:
        print('SYNC_REJECTED',str(e),file=sys.stderr)
        sys.exit(2)
