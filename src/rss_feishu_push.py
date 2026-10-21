#!/usr/bin/env python3
"""RSSHub (46 X accounts) -> Feishu hourly digest; local-only RSS, SQLite outbox."""
import argparse
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

# Every path and limit has a safe default matching the accepted v4 deployment.
# Override for local tests or additional independent installations; never put
# credentials in code, CLI arguments, or Git-tracked configuration.
ROOT = pathlib.Path(os.environ.get("RSS_FEISHU_ROOT", "/opt/rss-feishu-push"))
FEEDS_FILE = pathlib.Path(os.environ.get("RSS_FEEDS_FILE", str(ROOT / "feeds.json")))
STATE = pathlib.Path(os.environ.get("RSS_STATE_DB", str(ROOT / "state" / "push.sqlite3")))
CONFIG_FILE = pathlib.Path(os.environ.get("RSS_WEBHOOK_CONFIG", "/etc/rss-feishu-push.env"))
TZ = ZoneInfo(os.environ.get("RSS_TIMEZONE", "Asia/Shanghai"))
BASE = os.environ.get("RSSHUB_BASE_URL", "http://127.0.0.1:1200").rstrip("/") + "/twitter/user/"
KEYWORD = "RSS情报"
EXPECTED_FEED_COUNT = int(os.environ.get("RSS_EXPECTED_FEED_COUNT", "46"))
ITEM_LIMIT = 12
MAX_AGE_HOURS = 72
MIN_SEND_SECONDS = 55 * 60
FETCH_TIMEOUT = 15
CREATE_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=20000;
CREATE TABLE IF NOT EXISTS feeds(handle TEXT PRIMARY KEY, initialized_at INTEGER, last_success INTEGER, last_error TEXT);
CREATE TABLE IF NOT EXISTS articles(
 id TEXT PRIMARY KEY, handle TEXT NOT NULL, title TEXT NOT NULL,
 url TEXT NOT NULL, published INTEGER NOT NULL, discovered INTEGER NOT NULL,
 delivered INTEGER NOT NULL DEFAULT 0, batch_id INTEGER);
CREATE INDEX IF NOT EXISTS idx_articles_queued ON articles(delivered,batch_id,discovered);
CREATE TABLE IF NOT EXISTS batches(
 id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 next_try INTEGER, sent_at INTEGER, error_code TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, val TEXT NOT NULL);
"""

def log(event, **fields):
    print(json.dumps({"event":event, **fields}, ensure_ascii=False), flush=True)

def dbopen():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(STATE), timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(CREATE_SQL)
    db.commit()
    return db

def getmeta(db, key, default=None):
    row = db.execute("SELECT val FROM meta WHERE key=?", (key,)).fetchone()
    return row["val"] if row else default

def setmeta(db, key, value):
    db.execute("INSERT INTO meta(key,val) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET val=excluded.val", (key, str(value)))

def loadfeeds():
    data = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))
    result = data.get("feeds")
    if not isinstance(result,list) or len(result)!=EXPECTED_FEED_COUNT or len({a["handle"].lower() for a in result}) != EXPECTED_FEED_COUNT:
        raise RuntimeError("FEEDS_COUNT_OR_UNIQUENESS_MISMATCH")
    for f in result:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", f["handle"]):
            raise RuntimeError("INVALID_HANDLE")
    return result

def parse_pubdate(value, now):
    if not value:
        return now
    try:
        value = email.utils.parsedate_to_datetime(value)
        if value.tzinfo is None: value = value.replace(tzinfo=dt.timezone.utc)
        return int(value.timestamp())
    except Exception:
        return now

def plain(value, maxlen=170):
    s = re.sub(r"<[^>]+>", " ", str(value or ""))
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()[:maxlen]

def normalize_url(value, handle):
    try:
        u = urllib.parse.urlsplit(value or "")
        host = (u.hostname or "").lower()
        if u.scheme != "https" or host not in ("x.com","twitter.com","mobile.twitter.com"):
            return "https://x.com/" + handle
        return urllib.parse.urlunsplit(("https",host,u.path,"",""))
    except Exception:
        return "https://x.com/" + handle

def fetch(feed):
    handle = feed["handle"]
    req = urllib.request.Request(BASE + handle,
        headers={"User-Agent":"RSSFeishuDigest/1.0","Accept":"application/rss+xml, application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            if resp.status != 200: return (handle, None, "HTTP_"+str(resp.status))
            body = resp.read(2500000)
            ctype = resp.headers.get("Content-Type","").lower()
            if "xml" not in ctype: return (handle,None,"NON_XML")
        root = ET.fromstring(body)
        if root.tag.lower().split("}")[-1] not in ("rss","rdf","feed"):
            return (handle,None,"INVALID_ROOT")
        now=int(time.time()); posts=[]
        for i in root.findall(".//item"):
            title = plain(i.findtext("title"),160)
            link = normalize_url(i.findtext("link"), handle)
            guid = plain(i.findtext("guid"),250)
            pub = parse_pubdate(i.findtext("pubDate"),now)
            if not title: continue
            # Link generally holds the stable X status id; GUID is fallback.
            unique = link if "/status/" in link else (guid or (title + str(pub)))
            aid = hashlib.sha256((handle.lower()+"|"+unique).encode()).hexdigest()
            posts.append({"id":aid,"handle":handle,"title":title,"url":link,"published":pub})
        if not posts: return (handle,None,"EMPTY_FEED")
        return (handle, posts, None)
    except urllib.error.HTTPError as e:
        return (handle,None,"HTTP_"+str(e.code))
    except (TimeoutError, OSError, ValueError, ET.ParseError) as e:
        return (handle,None,type(e).__name__)
    except Exception as e:
        return (handle,None,"UNKNOWN_"+type(e).__name__)

def poll(db,feeds):
    now = int(time.time()); checked=0; failed=[]; baseline=0; new=0; suppressed=0
    poll_started=time.monotonic()
    # Three bounded HTTP workers limit worst-case 46-source polling to
    # ceil(46/3)*15s (~240s), below the 420s service deadline.
    # Keep modest concurrency to avoid overwhelming the X upstream.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(fetch,feeds))
    for handle,posts,err in results:
        checked+=1
        existing = db.execute("SELECT initialized_at FROM feeds WHERE handle=?",(handle,)).fetchone()
        first = (not existing or not existing["initialized_at"])
        if err:
            db.execute("INSERT INTO feeds(handle,last_error) VALUES (?,?) ON CONFLICT(handle) DO UPDATE SET last_error=excluded.last_error",(handle,err))
            failed.append(handle+":"+err)
            continue
        if first:
            db.execute("INSERT INTO feeds(handle,initialized_at,last_success,last_error) VALUES (?,?,?,NULL) ON CONFLICT(handle) DO UPDATE SET initialized_at=excluded.initialized_at,last_success=excluded.last_success,last_error=NULL",(handle,now,now))
        else:
            db.execute("UPDATE feeds SET last_success=?,last_error=NULL WHERE handle=?",(now,handle))
        init_time = now if first else int(existing["initialized_at"])
        for v in posts:
            stale = v["published"] < init_time-900 or v["published"] < now-MAX_AGE_HOURS*3600 or v["published"] > now+7200
            done = first or stale
            cur=db.execute("INSERT OR IGNORE INTO articles(id,handle,title,url,published,discovered,delivered) VALUES (?,?,?,?,?,?,?)",
                (v["id"],handle,v["title"],v["url"],v["published"],now,1 if done else 0))
            if cur.rowcount:
                if first: baseline+=1
                elif stale: suppressed+=1
                else: new+=1
    db.commit()
    log("rss_polled",checked=checked,source_ok=checked-len(failed),source_failed=len(failed),
        baseline_inserted=baseline,new_queued=new,stale_suppressed=suppressed,
        duration_seconds=round(time.monotonic()-poll_started,1),
        failed_handles=failed[:46])
    return checked-len(failed),new,failed

def webhook():
    # Dedicated RSS-only file: no read or fallback to daily-report webhook.
    if not CONFIG_FILE.is_file():
        raise RuntimeError("RSS_WEBHOOK_FILE_MISSING")
    matches = []
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(?:export\s+)?FEISHU_WEBHOOK_URL_RSS\s*=\s*(.*)$", line)
        if m:
            val=m.group(1).strip()
            if len(val)>1 and val[0]==val[-1] and val[0] in ("'", '"'):
                val=val[1:-1]
            matches.append(val)
    if len(matches)!=1 or not matches[0]:
        raise RuntimeError("RSS_WEBHOOK_MISSING_OR_DUPLICATE")
    value = matches[0]
    url=urllib.parse.urlsplit(value)
    if url.scheme!="https" or url.hostname not in ("open.feishu.cn","open.larksuite.com") or not url.path.startswith("/open-apis/bot/v2/hook/"):
        raise RuntimeError("RSS_WEBHOOK_INVALID")
    return value

class KnownFailure(Exception):pass
class UncertainFailure(Exception):pass

def send_payload(payload):
    kind=payload.get("msg_type")
    if kind=="interactive":
        title=payload.get("card",{}).get("header",{}).get("title",{}).get("content","")
    elif kind=="post":
        # Legacy in-flight batches may still contain the previous rich-text
        # format. Never mutate an already-sent or ambiguous batch.
        title=payload.get("content",{}).get("post",{}).get("zh_cn",{}).get("title","")
    else:
        raise KnownFailure("UNSUPPORTED_MESSAGE_TYPE")
    if KEYWORD not in title or "AI工作日报" in json.dumps(payload,ensure_ascii=False):
        raise KnownFailure("RSS_CONTENT_KEYWORD_INVALID")
    target = webhook()
    encoded = json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
    if len(encoded)>19000: raise KnownFailure("BODY_TOO_LARGE")
    request=urllib.request.Request(target,encoded,{"Content-Type":"application/json; charset=utf-8"},method="POST")
    try:
        with urllib.request.urlopen(request,timeout=18) as r:
            data=r.read(4000)
            status=r.status
    except urllib.error.HTTPError as exc:
        # Provider HTTP errors are a documented rejection and can be retried.
        raise KnownFailure("HTTP_"+str(exc.code))
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        # Ambiguous network timeout: it might have been delivered. Never blind retry.
        raise UncertainFailure(type(exc).__name__)
    if status != 200: raise KnownFailure("HTTP_"+str(status))
    try: obj=json.loads(data)
    except ValueError: raise UncertainFailure("INVALID_JSON_RESPONSE")
    code=obj.get("code",obj.get("StatusCode",None))
    if str(code) not in ("0",):raise KnownFailure("PROVIDER_CODE_"+str(code)[:12])
    return True

def safe_card_text(value, limit=180):
    # X content is untrusted text. Disallow mentions/markdown or HTML-like
    # constructs from turning into commands, card tags or unwanted group @all.
    text=plain(value,limit).replace("<","〈").replace(">","〉")
    # Embedded third-party URLs belong in the original X post, not the brief.
    text=re.sub(r"https?://[^\s]+", "（外部链接见原帖）", text, flags=re.I)
    translation=str.maketrans({"\\":"＼","*":"＊","_":"＿","[":"［","]":"］"})
    return text.translate(translation)

# Deterministic, non-AI topic classification from the original post text.
# Avoid inferring themes from author identity or inventing an article headline.
TOPIC_RULES = (
    ("AI Agent / 自动化", r"(?i)(?<![a-z0-9])(agent|agents|mcp|workflow|workflows|codex|openclaw|n8n)(?![a-z0-9])|智能体|多智能体|工作流|自动化编排|代理编程"),
    ("AI 模型 / 开发", r"(?i)(?<![a-z0-9])(gpt|chatgpt|claude|gemini|deepseek|qwen|llm|llama|rag|api|token)(?![a-z0-9])|大模型|模型推理|模型训练|提示词|开源模型|模型更新"),
    ("AI 产品 / 工具", r"(?i)(?<![a-z0-9])(saas|app|plugin|plugins|copilot|open.?source)(?![a-z0-9])|AI工具|AI应用|AI产品|应用发布|插件|产品发布|开源工具"),
    ("创业 / 商业化", r"(?i)(?<![a-z0-9])(revenue|indie.?hacker|monetiz(?:e|ation)|mrr)(?![a-z0-9])|创业|商业化|副业|营收|定价|收入增长|盈利|收费模式|付费订阅|商业模式"),
    ("出海 / 增长", r"(?i)(?<![a-z0-9])(seo|aso|growth|marketing)(?![a-z0-9])|出海|获客|独立站|流量增长|海外市场|海外用户|广告投放|用户增长"),
)
TOPIC_OTHER = "其他动态"

def topic_of(post):
    # This is a simple rule-based bucket, *not* a model prediction or summary.
    raw=plain(post.get("title",""),600)
    scores=[len(re.findall(pattern,raw)) for _,pattern in TOPIC_RULES]
    best=max(scores,default=0)
    if not best:
        return TOPIC_OTHER
    # Stable ties: maintain the curated category priority above.
    return TOPIC_RULES[scores.index(best)][0]

def compact_excerpt(text):
    # Shorter click-through excerpt prevents a single source from dominating
    # the topic. This is raw RSS text, NOT an AI-written summary.
    raw=safe_card_text(text,66).replace("↗","")
    if len(raw)>56:
        raw=raw[:55].rstrip()+"…"
    return raw or "查看原帖"

def card_summary_line(text):
    return {"tag":"div","text":{"tag":"lark_md","content":text}}

def build_post(items, now, remaining=0, test=False):
    # Keep all 12 posts in one card; merge the posts in each theme into ONE
    # Markdown div. No per-item buttons, horizontal rules, or blank paragraphs.
    curr=dt.datetime.fromtimestamp(now,TZ).strftime("%m-%d %H:%M")
    title=KEYWORD+("｜主题聚合【排版测试】" if test else "｜按主题速览")
    header={"title":{"tag":"plain_text","content":title+" · "+curr},"template":"blue"}
    elements=[]
    if test:
        intro="RSS情报 · 【排版测试】"+str(len(items))+" 条历史样例，仅验证紧凑显示，不作为新消息"
    else:
        intro="RSS情报 · 本期 "+str(len(items))+" 条"+(" · 待发 "+str(remaining)+" 条" if remaining else "")
    elements.append(card_summary_line(intro))
    grouped={label:[] for label,_ in TOPIC_RULES}
    grouped[TOPIC_OTHER]=[]
    for post in items:
        grouped[topic_of(post)].append(post)
    for theme, posts in grouped.items():
        if not posts:
            continue
        lines=["**"+theme+" · "+str(len(posts))+" 条**"]
        for idx,post in enumerate(posts,1):
            author=safe_card_text(post.get("name") or post["handle"],16).replace("↗","")
            handle=post["handle"]
            stamp=dt.datetime.fromtimestamp(int(post["published"]),TZ).strftime("%H:%M")
            excerpt=compact_excerpt(post.get("title",""))
            link=normalize_url(post.get("url",""),handle)
            # Numbered mini-entry: one concise metadata row, one clickable
            # original-text row. Shared topic div avoids button/card padding.
            lines.append("**"+str(idx).zfill(2)+" · "+author+"**  ·  "+stamp
                         +"\n　["+excerpt+"]("+link+")")
        elements.append(card_summary_line("\n".join(lines)))
    elements.append({"tag":"note","elements":[{
        "tag":"plain_text","content":"RSS情报 · 原帖文字摘录 · 主题按关键词归类 · 北京时间"}]})
    card={"config":{"wide_screen_mode":True},"header":header,"elements":elements}
    payload={"msg_type":"interactive","card":card}
    if len(elements)>12:
        raise KnownFailure("CARD_ELEMENTS_TOO_MANY")
    if len(json.dumps(payload,ensure_ascii=False).encode("utf-8"))>19000:
        raise KnownFailure("CARD_BODY_TOO_LARGE")
    return payload


def make_batch(db,feeds,now):
    # An interrupted/ambiguous delivery must not be resent automatically, but
    # it also must not block unrelated new articles forever.
    crashed=db.execute("UPDATE batches SET state='ambiguous',error_code='INTERRUPTED_DURING_SEND' WHERE state='sending'")
    if crashed.rowcount:
        db.commit()
        log("orphaned_delivery_requires_manual_review",count=crashed.rowcount)
    existing=db.execute("SELECT * FROM batches WHERE state IN ('pending','retry') ORDER BY id LIMIT 1").fetchone()
    if existing:return existing
    allrows=db.execute("SELECT * FROM articles WHERE delivered=0 AND batch_id IS NULL ORDER BY discovered,published LIMIT ?",(ITEM_LIMIT,)).fetchall()
    if not allrows: return None
    queued=db.execute("SELECT COUNT(*) FROM articles WHERE delivered=0 AND batch_id IS NULL").fetchone()[0]
    chosen=allrows[:ITEM_LIMIT]
    names={f["handle"]:f["name"] for f in feeds}
    payload=build_post([dict(r)|{"name":names.get(r["handle"],r["handle"])} for r in chosen],now,max(0,queued-len(chosen)))
    cur=db.execute("INSERT INTO batches(created,payload,state,attempts,next_try) VALUES (?,?, 'pending',0,?)",(now,json.dumps(payload,ensure_ascii=False),now))
    bid=cur.lastrowid
    db.executemany("UPDATE articles SET batch_id=? WHERE id=?",[(bid,row["id"]) for row in chosen])
    db.commit()
    return db.execute("SELECT * FROM batches WHERE id=?",(bid,)).fetchone()

def deliver(db,batch,now):
    if batch["state"] in ("sending","ambiguous"):
        log("delivery_requires_manual_review",batch_id=batch["id"],status=batch["state"])
        return
    if batch["next_try"] and now < batch["next_try"]:
        log("delivery_retry_not_due",batch_id=batch["id"])
        return
    last=int(getmeta(db,"last_sent_epoch","0"))
    if now-last < MIN_SEND_SECONDS:
        log("delivery_rate_limited",remaining_seconds=MIN_SEND_SECONDS-(now-last))
        return
    bid=batch["id"];attempt=batch["attempts"]+1
    db.execute("UPDATE batches SET state='sending',attempts=? WHERE id=?",(attempt,bid))
    db.commit()
    try:
        send_payload(json.loads(batch["payload"]))
    except KnownFailure as e:
        delay=min(3600,60*(2**min(attempt,6)))
        db.execute("UPDATE batches SET state='retry', next_try=?,error_code=? WHERE id=?",(now+delay,str(e),bid))
        db.commit()
        log("feishu_rejected_will_retry",batch_id=bid,attempt=attempt,reason=str(e),next_try_seconds=delay)
        return
    except UncertainFailure as e:
        db.execute("UPDATE batches SET state='ambiguous',error_code=? WHERE id=?",(str(e),bid))
        db.commit()
        log("feishu_outcome_unknown_manual_review",batch_id=bid,reason=str(e))
        return
    except Exception as e:
        db.execute("UPDATE batches SET state='ambiguous',error_code=? WHERE id=?",(type(e).__name__,bid))
        db.commit()
        log("feishu_outcome_unknown_manual_review",batch_id=bid,reason=type(e).__name__)
        return
    db.execute("UPDATE batches SET state='sent',sent_at=?,error_code=NULL WHERE id=?",(now,bid))
    rows=db.execute("UPDATE articles SET delivered=1 WHERE batch_id=?",(bid,))
    setmeta(db,"last_sent_epoch",now)
    db.commit()
    log("feishu_digest_sent",batch_id=bid,article_count=rows.rowcount,attempt=attempt)

def test_send(db):
    if getmeta(db,"rss_bot_test_sent"):raise RuntimeError("RSS_BOT_TEST_ALREADY_SENT")
    if getmeta(db,"rss_bot_test_sending"):raise RuntimeError("RSS_BOT_TEST_UNCERTAIN_PREVIOUS")
    now=int(time.time())
    setmeta(db,"rss_bot_test_sending",now)
    db.commit()
    try:send_payload(build_post([],now,test=True))
    except KnownFailure as e:
        db.execute("DELETE FROM meta WHERE key='rss_bot_test_sending'");db.commit()
        log("acceptance_test_provider_rejected",reason=str(e));raise
    except Exception as e:
        log("acceptance_test_unknown_check_feishu_first",reason=type(e).__name__);raise
    setmeta(db,"rss_bot_test_sent",now)
    db.execute("DELETE FROM meta WHERE key='rss_bot_test_sending'")
    db.commit()
    log("acceptance_test_confirmed_api_success",test_time=dt.datetime.fromtimestamp(now,TZ).isoformat())

def migrate_routing(db):
    # Inflight unknown/sent batches are immutable for at-most-once delivery.
    # Only clearly undelivered batches may be rewritten for the new bot.
    rows=db.execute("SELECT id,payload FROM batches WHERE state IN ('pending','retry')").fetchall()
    changed=0
    for row in rows:
        payload=json.loads(row["payload"])
        content=json.dumps(payload,ensure_ascii=False)
        if "AI工作日报" in content:
            content=content.replace("AI工作日报","RSS情报")
            rewritten=json.loads(content)
            assert KEYWORD in rewritten["content"]["post"]["zh_cn"]["title"]
            db.execute("UPDATE batches SET payload=? WHERE id=?",(json.dumps(rewritten,ensure_ascii=False),row["id"]))
            changed+=1
    setmeta(db,"active_feishu_route","rss_separate_bot_v1")
    db.commit()
    log("rss_route_migration_complete",migrated_pending_batches=changed)

def send_card_test(db,feeds):
    # Format-only preview: use two pre-existing public X post links, not new
    # discovery or delivery. Marked as a test in title and body.
    if getmeta(db,"rss_card_v4_test_sent"):
        raise RuntimeError("RSS_CARD_V4_TEST_ALREADY_SENT")
    if getmeta(db,"rss_card_v4_test_sending"):
        raise RuntimeError("RSS_CARD_V4_TEST_OUTCOME_UNCERTAIN")
    existing=db.execute(
        "SELECT * FROM articles WHERE delivered=1 AND url LIKE '%/status/%' "
        "ORDER BY published DESC LIMIT 150").fetchall()
    if len(existing)<2:
        raise RuntimeError("NOT_ENOUGH_EXAMPLES_FOR_CARD_PREVIEW")
    # Preview up to 8 already delivered articles, preferring 3 from one
    # topic to exercise visual separation between multiple rows.
    categories={}
    for row in existing:
        obj=dict(row)
        label=topic_of(obj)
        if len(categories.get(label,[]))<3:
            categories.setdefault(label,[]).append(obj)
    selected=[]
    for topic,rows in categories.items():
        selected.extend(rows[:3])
        if len(selected)>=8:
            break
    names={f["handle"]:f["name"] for f in feeds}
    posts=[row|{"name":names.get(row["handle"],row["handle"])} for row in selected[:8]]
    now=int(time.time())
    payload=build_post(posts,now,test=True)
    setmeta(db,"rss_card_v4_test_sending",now)
    db.commit()
    try: send_payload(payload)
    except KnownFailure as e:
        db.execute("DELETE FROM meta WHERE key='rss_card_v4_test_sending'")
        db.commit()
        log("card_test_rejected",reason=str(e))
        raise
    except Exception as e:
        log("card_test_outcome_uncertain_check_feishu_before_retry",error=type(e).__name__)
        raise
    setmeta(db,"rss_card_v4_test_sent",now)
    db.execute("DELETE FROM meta WHERE key='rss_card_v4_test_sending'")
    db.commit()
    log("card_test_api_success",example_count=len(posts),
        sent_at=dt.datetime.fromtimestamp(now,TZ).isoformat())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("command",choices=("bootstrap","run","send-test","send-card-test","status","self-test","migrate-routing","validate-rss-webhook"))
    args=ap.parse_args()
    feeds=loadfeeds()
    if args.command=="self-test":
        assert len(feeds)==EXPECTED_FEED_COUNT
        x=build_post([{"published":int(time.time()),"name":"测试","handle":"dotey","title":"测试内容","url":"https://x.com/dotey/status/123"}],int(time.time()))
        assert x["msg_type"]=="interactive"
        assert KEYWORD in x["card"]["header"]["title"]["content"]
        assert "AI工作日报" not in json.dumps(x,ensure_ascii=False)
        assert KEYWORD in build_post([],int(time.time()),test=True)["card"]["header"]["title"]["content"]
        assert len(x["card"]["elements"])==3
        assert "https://x.com/dotey/status/123" in x["card"]["elements"][1]["text"]["content"]
        assert "作者：" not in x["card"]["elements"][1]["text"]["content"]
        assert "AI摘要" not in json.dumps(x,ensure_ascii=False)
        assert "重要度" not in json.dumps(x,ensure_ascii=False)
        assert len(json.dumps(x,ensure_ascii=False).encode())<19000
        print("SELF_TEST_OK");return
    if args.command=="validate-rss-webhook":
        target=webhook()
        print(json.dumps({"rss_webhook_configured":True,"key":"FEISHU_WEBHOOK_URL_RSS",
                          "required_keyword":KEYWORD,"host":urllib.parse.urlsplit(target).hostname},ensure_ascii=False))
        return
    db=dbopen()
    try:
        if args.command=="migrate-routing":
            migrate_routing(db)
            return
        if args.command=="status":
            for table in ("feeds","articles","batches"):
                log("db_count",table=table,count=db.execute("SELECT count(*) FROM "+table).fetchone()[0])
            log("push_status",initialized=db.execute("SELECT count(*) FROM feeds WHERE initialized_at IS NOT NULL").fetchone()[0],
                pending_articles=db.execute("SELECT count(*) FROM articles WHERE delivered=0").fetchone()[0],
                batch_states={r["state"]:r["count"] for r in db.execute("SELECT state,count(*) count FROM batches GROUP BY state")},
                old_bot_test_sent=bool(getmeta(db,"acceptance_test_sent")),
                rss_bot_test_sent=bool(getmeta(db,"rss_bot_test_sent")),
                rss_card_v2_test_sent=bool(getmeta(db,"rss_card_v2_test_sent")),
                rss_card_v3_test_sent=bool(getmeta(db,"rss_card_v3_test_sent")),
                rss_card_v4_test_sent=bool(getmeta(db,"rss_card_v4_test_sent")))
            return
        if args.command=="send-test":test_send(db);return
        if args.command=="send-card-test":send_card_test(db,feeds);return
        good,added,failed=poll(db,feeds)
        if args.command=="bootstrap":
            log("baseline_initialized",feeds_initialized=db.execute("SELECT count(*) FROM feeds WHERE initialized_at IS NOT NULL").fetchone()[0],
                articles_seen=db.execute("SELECT count(*) FROM articles").fetchone()[0],
                active_queue=db.execute("SELECT count(*) FROM articles WHERE delivered=0").fetchone()[0])
            return
        now=int(time.time())
        batch=make_batch(db,feeds,now)
        if batch:deliver(db,batch,now)
        else:log("no_new_articles_to_send")
    finally:
        db.close()

if __name__=="__main__":main()
