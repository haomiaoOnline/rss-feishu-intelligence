"""Multi-group routing and group-scoped Feishu outbox.

The existing legacy single-group articles/batches tables remain unchanged.
This module only creates additive schema and routes *new eligible* posts.
"""
import argparse
import json
import os
import pathlib
import re
import secrets
import sqlite3
import sys
import time
from contextlib import contextmanager

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rss_feishu_push as rss

SECRET_DIR = pathlib.Path(os.environ.get("RSS_GROUP_SECRET_DIR", "/etc/rss-feishu-groups"))
ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")
DDL = """
CREATE TABLE IF NOT EXISTS admin_groups(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
 enabled_since INTEGER NOT NULL DEFAULT 0,
 batch_limit INTEGER NOT NULL DEFAULT 12,
 interval_seconds INTEGER NOT NULL DEFAULT 3300,
 last_sent INTEGER NOT NULL DEFAULT 0,
 created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS admin_members(
 group_id TEXT NOT NULL REFERENCES admin_groups(id),
 handle TEXT NOT NULL, since INTEGER NOT NULL,
 PRIMARY KEY(group_id,handle));
CREATE TABLE IF NOT EXISTS admin_batches(
 id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL,
 created INTEGER NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 next_try INTEGER NOT NULL DEFAULT 0, sent_at INTEGER, error_code TEXT);
CREATE INDEX IF NOT EXISTS idx_admin_batches_status ON admin_batches(group_id,state,id);
CREATE TABLE IF NOT EXISTS admin_deliveries(
 group_id TEXT NOT NULL, article_id TEXT NOT NULL,
 batch_id INTEGER NOT NULL, state TEXT NOT NULL,
 PRIMARY KEY(group_id,article_id));
CREATE INDEX IF NOT EXISTS idx_admin_deliveries_batch ON admin_deliveries(batch_id);
CREATE TABLE IF NOT EXISTS admin_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, happened INTEGER NOT NULL,
 action TEXT NOT NULL, subject TEXT NOT NULL);
"""

def initialize(db):
    db.executescript(DDL)
    # Additive v3 migration preserves all group delivery history.
    group_columns={r[1] for r in db.execute("PRAGMA table_info(admin_groups)")}
    if "archived" not in group_columns:
        db.execute("ALTER TABLE admin_groups ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
    columns={r[1] for r in db.execute("PRAGMA table_info(articles)")}
    if "eligible_for_groups" not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN eligible_for_groups INTEGER NOT NULL DEFAULT 0")
    db.commit()

def audit(db, action, subject):
    db.execute("INSERT INTO admin_audit(happened,action,subject) VALUES (?,?,?)",
               (int(time.time()),action,str(subject)[:100]))

def validate_id(value):
    if not ID_PATTERN.fullmatch(str(value)):
        raise ValueError("INVALID_GROUP_ID")
    return str(value)

def all_groups(db):
    return [dict(r) for r in db.execute(
        "SELECT * FROM admin_groups WHERE archived=0 ORDER BY created,id")]

def create_group(db, name, limit=12, interval=3300):
    name=str(name).strip()
    if not name or len(name)>64 or any(ord(c)<32 for c in name):
        raise ValueError("INVALID_GROUP_NAME")
    limit=int(limit);interval=int(interval)
    if not 1<=limit<=12 or not 300<=interval<=86400:
        raise ValueError("INVALID_GROUP_RATE")
    gid=secrets.token_hex(6)
    now=int(time.time())
    db.execute("INSERT INTO admin_groups(id,name,batch_limit,interval_seconds,created,updated)"
               " VALUES(?,?,?,?,?,?)",(gid,name,limit,interval,now,now))
    audit(db,"group_created",gid)
    db.commit()
    return gid

def update_group(db,gid, name, enabled, limit, interval, secret_dir=SECRET_DIR,
                 *, commit=True, webhook_pending=False):
    gid=validate_id(gid)
    current=db.execute("SELECT * FROM admin_groups WHERE id=?",(gid,)).fetchone()
    if current is None or current["archived"]:raise ValueError("GROUP_NOT_FOUND")
    name=str(name).strip()
    limit=int(limit);interval=int(interval)
    if not name or len(name)>64 or not 1<=limit<=12 or not 300<=interval<=86400:
        raise ValueError("INVALID_GROUP_CONFIGURATION")
    enabled=bool(enabled)
    if current["enabled"] and not enabled:
        # Pausing a group must never later send stale pre-pause pending batches.
        _cancel_unsent(db,gid,"GROUP_PAUSED")
    if enabled and not (webhook_pending or has_secret(gid,secret_dir)):
        raise ValueError("GROUP_WEBHOOK_NOT_CONFIGURED")
    if enabled and db.execute("SELECT COUNT(*) FROM admin_members WHERE group_id=?",(gid,)).fetchone()[0]==0:
        raise ValueError("GROUP_NEEDS_SOURCES")
    now=int(time.time())
    since=now if enabled and not current["enabled"] else current["enabled_since"]
    db.execute("UPDATE admin_groups SET name=?,enabled=?,enabled_since=?,"
               "batch_limit=?,interval_seconds=?,updated=? WHERE id=?",
               (name,int(enabled),since,limit,interval,now,gid))
    audit(db,"group_updated",gid)
    if commit:db.commit()

def set_members(db,gid,handles,available,*,commit=True):
    gid=validate_id(gid)
    if db.execute("SELECT 1 FROM admin_groups WHERE id=?",(gid,)).fetchone() is None:
        raise ValueError("GROUP_NOT_FOUND")
    chosen=set(handles)
    allowed=set(available)
    if not chosen.issubset(allowed):
        raise ValueError("UNKNOWN_SOURCE_SELECTION")
    if len(chosen)>rss.MAX_FEEDS:
        raise ValueError("TOO_MANY_SOURCES")
    existing={r[0] for r in db.execute("SELECT handle FROM admin_members WHERE group_id=?",(gid,))}
    removed=existing-chosen
    now=int(time.time())
    for handle in sorted(chosen-existing):
        db.execute("INSERT INTO admin_members(group_id,handle,since) VALUES (?,?,?)",(gid,handle,now))
    if removed:
        # Cancel affected unsent batches, but allow still-subscribed articles
        # to be queued anew. No sent/ambiguous replay.
        batch_ids={r[0] for r in db.execute(
            "SELECT DISTINCT b.id FROM admin_batches b JOIN admin_deliveries d ON d.batch_id=b.id "
            "JOIN articles a ON a.id=d.article_id WHERE b.group_id=? "
            "AND b.state IN ('pending','retry') AND a.handle IN ("+
            ",".join("?" for _ in removed)+")",(gid,*sorted(removed)))}
        for bid in batch_ids:
            db.execute("UPDATE admin_batches SET state='cancelled',error_code='MEMBERSHIP_CHANGED' WHERE id=?",(bid,))
            # The cancelled packet is never sent. Remove only its unsent
            # membership dedup claims; immutable sent/ambiguous are untouched.
            db.execute("DELETE FROM admin_deliveries WHERE batch_id=?",(bid,))
        db.executemany("DELETE FROM admin_members WHERE group_id=? AND handle=?",
                       [(gid,h) for h in removed])
    audit(db,"group_sources_updated",gid)
    if commit:db.commit()
    return {"added":len(chosen-existing),"removed":len(removed)}

def save_group_settings(db,gid,name,enabled,limit,interval,selected,available,
                        webhook="",secret_dir=SECRET_DIR):
    """Preflight group settings, then one transaction for membership + metadata.

    Secret file may be replaced only after database invariants validate;
    invalid input cannot partially change selected sources or group state.
    """
    gid=validate_id(gid)
    current=db.execute("SELECT archived FROM admin_groups WHERE id=?",(gid,)).fetchone()
    if current is None or current["archived"]:
        raise ValueError("GROUP_NOT_FOUND")
    name=str(name).strip()
    try:
        limit=int(limit);interval=int(interval)
    except (TypeError,ValueError):
        raise ValueError("INVALID_GROUP_CONFIGURATION") from None
    if (not name or len(name)>64 or any(ord(c)<32 for c in name)
        or not 1<=limit<=12 or not 300<=interval<=86400):
        raise ValueError("INVALID_GROUP_CONFIGURATION")
    selected=list(selected)
    if len(selected)!=len(set(selected)) or not set(selected).issubset(set(available)):
        raise ValueError("UNKNOWN_SOURCE_SELECTION")
    enabled=bool(enabled)
    if enabled and not selected:
        raise ValueError("GROUP_NEEDS_SOURCES")
    normalized=rss.validate_webhook_url(webhook) if webhook else ""
    if enabled and not (normalized or has_secret(gid,secret_dir)):
        raise ValueError("GROUP_WEBHOOK_NOT_CONFIGURED")
    with db:
        set_members(db,gid,selected,available,commit=False)
        update_group(db,gid,name,enabled,limit,interval,secret_dir,
                     commit=False,webhook_pending=bool(normalized))
        if normalized:
            store_secret(gid,normalized,secret_dir)
    return {"saved":True,"member_count":len(selected)}

def _cancel_unsent(db,gid,reason):
    rows=db.execute("SELECT id FROM admin_batches WHERE group_id=? "
                    "AND state IN ('pending','retry')",(gid,)).fetchall()
    for row in rows:
        db.execute("UPDATE admin_batches SET state='cancelled',error_code=? WHERE id=?",
                   (reason,row["id"]))
        db.execute("DELETE FROM admin_deliveries WHERE batch_id=?",(row["id"],))

def archive_group(db,gid,confirmation):
    gid=validate_id(gid)
    if confirmation!="ARCHIVE":raise ValueError("ARCHIVE_REQUIRES_EXPLICIT_CONFIRMATION")
    row=db.execute("SELECT archived FROM admin_groups WHERE id=?",(gid,)).fetchone()
    if row is None or row["archived"]:raise ValueError("GROUP_NOT_FOUND")
    _cancel_unsent(db,gid,"GROUP_ARCHIVED")
    db.execute("UPDATE admin_groups SET archived=1,enabled=0,updated=? WHERE id=?",
               (int(time.time()),gid))
    audit(db,"group_archived",gid)
    db.commit()

def secret_path(gid, root=SECRET_DIR):
    return pathlib.Path(root)/(validate_id(gid)+".webhook")

def has_secret(gid,root=SECRET_DIR):
    return secret_path(gid,root).is_file()

def store_secret(gid, value, root=SECRET_DIR):
    gid=validate_id(gid)
    value=rss.validate_webhook_url(value)
    root=pathlib.Path(root)
    if root.is_symlink():
        raise ValueError("SECRET_DIRECTORY_SYMLINK_FORBIDDEN")
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.stat().st_mode & 0o077:
        raise ValueError("SECRET_DIRECTORY_MUST_BE_PRIVATE_0700")
    path=secret_path(gid,root)
    if path.is_symlink():
        raise ValueError("SECRET_SYMLINK_FORBIDDEN")
    from tempfile import NamedTemporaryFile
    import stat
    with NamedTemporaryFile(mode="w",encoding="utf-8",dir=root,prefix=".hook-",delete=False) as out:
        temp=pathlib.Path(out.name)
        out.write(value+"\n");out.flush();os.fsync(out.fileno())
    try:
        os.chmod(temp,0o600)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)

def read_secret(gid, root=SECRET_DIR):
    path=secret_path(gid,root)
    if path.is_symlink():
        raise ValueError("SECRET_SYMLINK_FORBIDDEN")
    if path.stat().st_mode & 0o077:
        raise ValueError("SECRET_FILE_MUST_BE_PRIVATE_0600")
    data=path.read_text(encoding="utf-8").strip()
    return rss.validate_webhook_url(data)

def _mark_crashes(db):
    rows=db.execute("SELECT id FROM admin_batches WHERE state='sending'").fetchall()
    for row in rows:
        db.execute("UPDATE admin_batches SET state='ambiguous',error_code='INTERRUPTED' WHERE id=?",(row["id"],))
        db.execute("UPDATE admin_deliveries SET state='ambiguous' WHERE batch_id=?",(row["id"],))
    if rows:db.commit()

def _make_batch(db,group,feeds,now):
    gid=group["id"]
    existing=db.execute("SELECT * FROM admin_batches WHERE group_id=? AND state IN ('pending','retry') "
                        "ORDER BY id LIMIT 1",(gid,)).fetchone()
    if existing:return existing
    rows=db.execute("""
      SELECT a.* FROM articles a
      JOIN admin_members m ON m.handle=a.handle AND m.group_id=?
      WHERE a.eligible_for_groups=1 AND a.discovered>=m.since
        AND a.discovered>=? AND a.published>=?
        AND NOT EXISTS (
          SELECT 1 FROM admin_deliveries d WHERE d.group_id=? AND d.article_id=a.id)
      ORDER BY a.discovered,a.published LIMIT ?
    """,(gid,group["enabled_since"],now-rss.MAX_AGE_HOURS*3600,gid,group["batch_limit"])).fetchall()
    if not rows:return None
    names={f["handle"]:f["name"] for f in feeds}
    payload=rss.build_post([dict(row)|{"name":names.get(row["handle"],row["handle"])}
                            for row in rows],now)
    title=payload["card"]["header"]["title"]
    title["content"]="RSS情报｜"+rss.safe_card_text(group["name"],20)+"｜主题速览"
    cur=db.execute("INSERT INTO admin_batches(group_id,created,payload,state) VALUES (?,?,?,'pending')",
                   (gid,now,json.dumps(payload,ensure_ascii=False)))
    bid=cur.lastrowid
    db.executemany("INSERT INTO admin_deliveries(group_id,article_id,batch_id,state) "
                   "VALUES (?,?,?,'pending')",[(gid,row["id"],bid) for row in rows])
    db.commit()
    return db.execute("SELECT * FROM admin_batches WHERE id=?",(bid,)).fetchone()

def dispatch(db,feeds,send=None,secret_dir=SECRET_DIR):
    """One fresh batch per active group, independent Feishu target/state."""
    initialize(db)
    _mark_crashes(db)
    now=int(time.time())
    if send is None:send=rss.send_payload
    result=[]
    for group in all_groups(db):
        if not group["enabled"]:continue
        gid=group["id"]
        if now-int(group["last_sent"])<group["interval_seconds"]:
            result.append((gid,"rate_limited"));continue
        if not has_secret(gid,secret_dir):
            result.append((gid,"missing_webhook"));continue
        batch=_make_batch(db,group,feeds,now)
        if batch is None:
            result.append((gid,"no_new_articles"));continue
        if batch["next_try"] and batch["next_try"]>now:
            result.append((gid,"retry_not_due"));continue
        bid=batch["id"];attempt=batch["attempts"]+1
        db.execute("UPDATE admin_batches SET state='sending',attempts=? WHERE id=?",(attempt,bid))
        db.execute("UPDATE admin_deliveries SET state='sending' WHERE batch_id=?",(bid,))
        db.commit()
        try:
            send(json.loads(batch["payload"]),read_secret(gid,secret_dir))
        except rss.KnownFailure as e:
            delay=min(3600,60*2**min(attempt,6))
            db.execute("UPDATE admin_batches SET state='retry',next_try=?,error_code=? WHERE id=?",
                       (now+delay,str(e)[:80],bid))
            db.execute("UPDATE admin_deliveries SET state='retry' WHERE batch_id=?",(bid,))
            result.append((gid,"retry"))
        except Exception as e:
            db.execute("UPDATE admin_batches SET state='ambiguous',error_code=? WHERE id=?",
                       (type(e).__name__,bid))
            db.execute("UPDATE admin_deliveries SET state='ambiguous' WHERE batch_id=?",(bid,))
            result.append((gid,"ambiguous"))
        else:
            db.execute("UPDATE admin_batches SET state='sent',sent_at=?,error_code=NULL WHERE id=?",(now,bid))
            db.execute("UPDATE admin_deliveries SET state='sent' WHERE batch_id=?",(bid,))
            db.execute("UPDATE admin_groups SET last_sent=? WHERE id=?",(now,gid))
            result.append((gid,"sent"))
        db.commit()
    return result

def summary(db):
    initialize(db)
    result=[]
    for g in all_groups(db):
        counts={r[0]:r[1] for r in db.execute("SELECT state,count(*) FROM admin_batches "
                "WHERE group_id=? GROUP BY state",(g["id"],))}
        row=dict(g);row["secret_configured"]=has_secret(g["id"],SECRET_DIR)
        row["members"]=db.execute("SELECT count(*) FROM admin_members WHERE group_id=?",(g["id"],)).fetchone()[0]
        row["batch_states"]=counts
        result.append(row)
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("dispatch","status","init"))
    args=parser.parse_args()
    db=rss.dbopen()
    try:
        initialize(db)
        if args.command=="dispatch":
            print(json.dumps({"group_dispatch":dispatch(db,rss.loadfeeds())},ensure_ascii=False))
        elif args.command=="status":
            print(json.dumps({"groups":summary(db)},ensure_ascii=False))
        else:print("GROUP_SCHEMA_OK")
    finally:db.close()

if __name__=="__main__":main()
