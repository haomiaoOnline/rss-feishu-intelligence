#!/usr/bin/env python3
"""One-source-of-truth feed list: validate, Feedbro OPML roundtrip and merge.

All mutations default to dry-run. Local OPML is an input, not automatically
pushed from browser extensions; Git-tracked config/feeds.json remains canonical.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"src"))
import rss_feishu_push as app

X_ROUTE=re.compile(r"^/twitter/user/([A-Za-z0-9_]{1,15})/?$")
X_PROFILE=re.compile(r"^/([A-Za-z0-9_]{1,15})/?$")
OPML_LIMIT=3_000_000

def identity(feed):
    if feed.get("type")=="rss":
        return "rss:"+app.rss_url(feed["url"])
    return "x:"+feed["handle"].lower()

def read_config(path):
    return json.loads(path.read_text(encoding="utf-8"))

def checked(feeds):
    app.normalize_feeds({"feeds":feeds})
    return {"feeds":feeds}

def from_url(value,group,name):
    try:
        u=urllib.parse.urlsplit(value.strip())
        host=(u.hostname or "").lower()
        if host in ("x.com","twitter.com","mobile.twitter.com") and not u.query:
            m=X_PROFILE.fullmatch(u.path)
            if m:return {"group":group,"name":name,"handle":m.group(1)}
        if host in ("rsshub.app","rsshub.rssforever.com","127.0.0.1","localhost") and not u.query:
            m=X_ROUTE.fullmatch(u.path)
            if m:return {"group":group,"name":name,"handle":m.group(1)}
        # Self-hosted RSSHub URL for X follows a fixed route, including
        # localhost or an explicitly chosen host; no network request made.
        if u.path.startswith("/twitter/user/") and not u.query:
            m=X_ROUTE.fullmatch(u.path)
            if m:return {"group":group,"name":name,"handle":m.group(1)}
        if u.scheme.lower()=="https":
            return {"type":"rss","group":group,"name":name,"url":app.rss_url(value)}
    except (ValueError,TypeError):
        pass
    raise ValueError("UNSUPPORTED_OR_UNSAFE_OPML_URL")

def parse_opml(path,skip_unsupported=False):
    if path.stat().st_size>OPML_LIMIT:
        raise ValueError("OPML_TOO_LARGE")
    root=ET.fromstring(path.read_bytes())
    if root.tag.lower()!="opml":
        raise ValueError("OPML_ROOT_INVALID")
    body=root.find("body")
    if body is None:raise ValueError("OPML_BODY_MISSING")
    entries=[];unsupported=[];seen=set()
    def walk(node,group="其他"):
        nonlocal entries
        for child in node.findall("outline"):
            title=(child.get("title") or child.get("text") or "").strip()
            url=child.get("xmlUrl")
            if url:
                try:
                    entry=from_url(url,group,title or "Unnamed RSS")
                    key=identity(entry)
                    if key not in seen:
                        entries.append(entry);seen.add(key)
                except ValueError:
                    unsupported.append(title or "Untitled")
            else:
                walk(child,title or group)
    walk(body)
    if unsupported and not skip_unsupported:
        raise ValueError("OPML_CONTAINS_UNSUPPORTED_SOURCES count="+str(len(unsupported))+
                         " (use --skip-unsupported only after review)")
    if not entries:
        raise ValueError("NO_USABLE_FEEDS_IN_OPML")
    return entries,unsupported

def to_opml(feeds,base):
    base=base.rstrip("/")
    if not base.startswith(("http://127.0.0.1:","http://localhost:","https://")):
        raise ValueError("UNSAFE_EXPORT_RSSHUB_BASE")
    root=ET.Element("opml",version="2.0")
    h=ET.SubElement(root,"head")
    ET.SubElement(h,"title").text="RSS情报统一订阅（Git config/feeds.json）"
    parent=ET.SubElement(root,"body")
    groups={}
    for f in feeds:
        group=f.get("group") or "其他"
        if group not in groups:
            groups[group]=ET.SubElement(parent,"outline",text=group,title=group)
        addr=f["url"] if f.get("type")=="rss" else base+"/twitter/user/"+f["handle"]
        ET.SubElement(groups[group],"outline",type="rss",text=f["name"],title=f["name"],xmlUrl=addr,
                      htmlUrl=(f["url"] if f.get("type")=="rss" else "https://x.com/"+f["handle"]))
    ET.indent(root,space="  ")
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)

def write_atomic(path,bytes_,backup=True):
    path.parent.mkdir(parents=True,exist_ok=True)
    if backup and path.exists():
        # Backup out of Git tree and root secrets, user data not copied to .git.
        dest=path.parent/".feed-backups"
        dest.mkdir(mode=0o700,exist_ok=True)
        cur=hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        copy=dest/("feeds-"+cur+".json")
        if not copy.exists():
            copy.write_bytes(path.read_bytes())
    fd,tmp=tempfile.mkstemp(prefix="."+path.name+"-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(bytes_);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o644)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("action",choices=("validate","list","import-opml","export-opml"))
    ap.add_argument("--config",type=Path,default=REPO/"config/feeds.json")
    ap.add_argument("--opml",type=Path)
    ap.add_argument("--mode",choices=("merge","replace"),default="merge")
    ap.add_argument("--skip-unsupported",action="store_true")
    ap.add_argument("--allow-remove",action="store_true")
    ap.add_argument("--apply",action="store_true")
    ap.add_argument("--output",type=Path,default=REPO/"exports/feedbro-subscriptions.opml")
    ap.add_argument("--rsshub-base",default="http://127.0.0.1:1200")
    args=ap.parse_args(argv)
    data=read_config(args.config)
    old=app.normalize_feeds(data)
    if args.action in ("validate","list"):
        print(json.dumps({"valid":True,"count":len(old),
                          "x_users":sum(f.get("type")!="rss" for f in old),
                          "rss_urls":sum(f.get("type")=="rss" for f in old)},
                         ensure_ascii=False))
        if args.action=="list":
            for f in old:print(f.get("group","其他"),f["name"],f.get("url",f["handle"]))
        return
    if args.action=="export-opml":
        encoded=to_opml(old,args.rsshub_base)
        print(json.dumps({"action":"export","count":len(old),"output":str(args.output),
                          "bytes":len(encoded),"dry_run":not args.apply},ensure_ascii=False))
        if args.apply:
            write_atomic(args.output,encoded,backup=False)
        return
    if not args.opml:ap.error("import-opml requires --opml")
    imported,unsupported=parse_opml(args.opml,args.skip_unsupported)
    old_by={identity(x):x for x in old}
    desired=({} if args.mode=="replace" else dict(old_by))
    for f in imported:desired[identity(f)]=f
    candidate=list(desired.values())
    new_keys=set(desired);old_keys=set(old_by)
    removed=old_keys-new_keys;added=new_keys-old_keys
    if removed and not args.allow_remove:
        raise RuntimeError("REMOVAL_REQUIRES_EXPLICIT_ALLOW_REMOVE")
    checked(candidate)
    print(json.dumps({"action":"import","mode":args.mode,"before":len(old),
                      "after":len(candidate),"added":len(added),"removed":len(removed),
                      "unsupported_skipped":len(unsupported),"dry_run":not args.apply},ensure_ascii=False))
    if args.apply:
        encoded=(json.dumps({"feeds":candidate},ensure_ascii=False,indent=2)+"\n").encode()
        write_atomic(args.config,encoded)

if __name__=="__main__":
    main()
