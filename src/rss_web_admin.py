"""Localhost-only RSS/Feishu group administration, Python standard library.

Deployment requires a root-owned 0600 admin token file, a private SSH tunnel,
and disabled Git feed sync. Never bind to a public interface.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import html
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import pathlib
import secrets
import sqlite3
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit, urlencode

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import rss_feishu_push as rss
import rss_groups as groups

ADMIN_TOKEN_FILE=pathlib.Path(os.environ.get("RSS_ADMIN_TOKEN_FILE","/etc/rss-feishu-admin.token"))
LOCK_FILE=pathlib.Path(os.environ.get("RSS_LOCK_FILE","/run/rss-feishu-push.lock"))
COOKIE_AGE=12*3600

def h(x):
    return html.escape(str(x),quote=True)

def get_token():
    p=ADMIN_TOKEN_FILE
    if not p.is_file() or p.is_symlink() or (p.stat().st_mode&0o077):
        raise RuntimeError("ADMIN_TOKEN_FILE_MUST_BE_PRIVATE_0600")
    token=p.read_text(encoding="utf-8").strip()
    if len(token)<32:raise RuntimeError("ADMIN_TOKEN_TOO_SHORT")
    return token.encode()

def _sign(token,value):
    return hmac.new(token,value.encode(),hashlib.sha256).hexdigest()

def session(token):
    stamp=str(int(time.time()))
    return stamp+"."+_sign(token,"session:"+stamp)

def validate_session(token,value):
    try:
        stamp,sig=value.split(".",1)
        age=int(time.time())-int(stamp)
        return 0<=age<COOKIE_AGE and hmac.compare_digest(sig,_sign(token,"session:"+stamp))
    except (ValueError,TypeError,AttributeError):
        return False

def csrf_token(token,sid):
    return _sign(token,"csrf:"+sid)

class AppServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self, address, handler, token):
        if address[0]!="127.0.0.1":
            raise ValueError("MANAGEMENT_BIND_MUST_BE_LOOPBACK")
        self.token=token
        self.failures={}
        super().__init__(address,handler)

class Handler(BaseHTTPRequestHandler):
    server_version="RSSAdmin/1.0"
    sys_version=""
    def log_message(self,fmt,*args):
        # Do not log form POST data, full URLs, cookie or webhook secrets.
        return

    def headers_common(self,status,ctype="text/html; charset=utf-8",length=0,extra=None):
        self.send_response(status)
        self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(length))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy",
             "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if extra:
            for k,v in extra.items():self.send_header(k,v)
        self.end_headers()

    def reply(self,status,body,extra=None):
        blob=body.encode("utf-8")
        self.headers_common(status,length=len(blob),extra=extra)
        self.wfile.write(blob)

    def redirect(self,where,extra=None):
        blob=b""
        head={"Location":where}
        if extra:head.update(extra)
        self.headers_common(303,length=0,extra=head)

    def _sid(self):
        try:
            cookie=cookies.SimpleCookie(self.headers.get("Cookie",""))
            return cookie["sid"].value if "sid" in cookie else ""
        except cookies.CookieError:return ""

    def authenticated(self):
        return validate_session(self.server.token,self._sid())

    def same_origin(self):
        host=self.headers.get("Host","")
        if host not in ("127.0.0.1","localhost") and not (
            host.startswith("127.0.0.1:") or host.startswith("localhost:")):
            return False
        origin=self.headers.get("Origin","")
        if origin:
            return origin in ("http://"+host,)
        return True

    def base(self,title,content,notice=""):
        sid=self._sid()
        tok=csrf_token(self.server.token,sid) if self.authenticated() else ""
        nav='<nav><a href="/">概览</a><a href="/sources">信源管理</a><a href="/groups">推送分组</a><a href="/history">投递记录</a></nav>' if tok else ""
        style="""body{background:#f2f5f4;color:#263c3b;font:15px -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;margin:0}
        header{background:#104c46;color:white;padding:18px 6%}header b{font-size:19px}
        main{max-width:1080px;margin:24px auto;padding:0 18px}
        nav{display:flex;gap:20px;background:white;padding:17px;margin:0 0 22px;border-radius:12px}
        a{color:#0b776b;text-decoration:none;font-weight:600}
        section{background:white;border-radius:12px;padding:24px;margin-bottom:17px;box-shadow:0 3px 10px #0a484711}
        label{display:inline-block;margin:8px 10px 8px 0}
        input,select{border:1px solid #c9d6d2;border-radius:8px;padding:9px;font:inherit;max-width:96%}
        input[type=checkbox]{width:17px;height:17px}
        button{padding:10px 16px;background:#0b776b;border:0;border-radius:8px;color:white;cursor:pointer;font:inherit}
        button.secondary{background:#667a75}.danger{background:#b84b4b}
        table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #e5eae8;text-align:left;padding:10px}
        .muted{color:#69817a;font-size:13px}.error{background:#fff0e9;border-left:4px solid #d56337;padding:12px}
        .ok{background:#ecf8f2;border-left:4px solid #0d8360;padding:12px}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
        .item{border:1px solid #e2eae7;border-radius:9px;padding:10px}.row{display:flex;flex-wrap:wrap;gap:12px}
        code{font-size:12px;white-space:normal}"""
        msg=('<div class="ok">'+h(notice)+'</div>' if notice else "")
        return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>'+h(title)+' - RSS 情报管理</title><style>'+style+'</style></head>'
                '<body><header><b>RSS 情报管理台</b>　<small>多信源 · 多飞书群 · 不重推</small></header>'
                '<main>'+nav+msg+content+'</main></body></html>')

    def input_csrf(self):
        return '<input type="hidden" name="csrf" value="'+csrf_token(self.server.token,self._sid())+'">'

    def read_form(self):
        try:n=int(self.headers.get("Content-Length","0"))
        except ValueError:raise ValueError("INVALID_FORM_LENGTH")
        if not 0<n<=65000:raise ValueError("FORM_SIZE_REJECTED")
        data=self.rfile.read(n).decode("utf-8","strict")
        return parse_qs(data,keep_blank_values=True,max_num_fields=180)

    @staticmethod
    def val(form,key,default=""):
        return form.get(key,[default])[0]

    def fail(self,message,status=400):
        self.reply(status,self.base("请求失败",'<section><h2>操作未完成</h2><div class="error">'+h(message)+'</div>'
                            '<p><a href="/">返回管理台</a></p></section>'))

    @contextmanager
    def db(self):
        db=rss.dbopen()
        try:
            groups.initialize(db)
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def do_GET(self):
        if not self.same_origin():
            self.reply(400,"请求来源无效");return
        path=urlsplit(self.path).path
        if path=="/health":
            self.reply(200,"ok");return
        if path=="/login":
            page=self.base("管理员登录","""<section><h2>管理员登录</h2><p class="muted">仅允许通过 SSH 隧道访问的本机管理台</p>
              <form method="post" action="/login"><label>管理员访问口令
              <input type="password" name="token" autocomplete="current-password" required></label>
              <button>登录</button></form></section>""")
            self.reply(200,page);return
        if not self.authenticated():
            self.redirect("/login");return
        try:self.render(path)
        except ValueError as exc:self.fail(str(exc),404)
        except (sqlite3.Error, OSError) as exc:self.fail("读取操作失败，请检查服务状态",500)

    def render(self,path):
        feeds=rss.normalize_feeds(json.loads(rss.FEEDS_FILE.read_text(encoding="utf-8")))
        with self.db() as db:
            if path=="/":
                gr=groups.summary(db)
                now=int(time.time())
                pending=db.execute("SELECT COUNT(*) FROM articles WHERE delivered=0").fetchone()[0]
                body='<section><h2>平台概览</h2><div class="grid">'+''.join(
                    '<div class="item"><strong>'+h(v)+'</strong><br><span class="muted">'+h(k)+'</span></div>' for k,v in
                    (("总信源",len(feeds)),("启用信源",sum(f.get("enabled",True) for f in feeds)),
                     ("新建推送分组",len(gr)),("原 RSS 群待发",pending)))+'</div></section>'
                body+='<section><h3>系统保留：原 RSS 情报群</h3><p class="muted">原有 v4 群机器人和 SQLite 批次仍由旧通道发送，不会被这里的群组重复投递。</p></section>'
                body+='<section><h3>最近分组</h3>'+''.join(
                   '<p><a href="/groups/'+h(g["id"])+'">'+h(g["name"])+'</a> · '+("启用" if g["enabled"] else "停用")+
                   ' · '+str(g["members"])+' 个信源</p>' for g in gr)+'</section>'
            elif path=="/sources":
                body='<section><h2>新增信源</h2><form action="/sources/add" method="post">'+self.input_csrf()
                body+='<label>类型 <select name="type"><option value="x_user">X 博主</option><option value="rss">通用 HTTPS RSS</option></select></label>'
                for name,label,placeholder in (("handle","X 用户名","dotey"),("url","RSS URL","https://example.org/rss.xml"),
                    ("name","显示名称","作者/网站名称"),("group","归类","AI / 增长")):
                    body+='<label>'+label+' <input name="'+name+'" placeholder="'+placeholder+'"></label>'
                body+='<button>添加信源</button></form></section>'
                body+='<section><h2>全部信源（'+str(len(feeds))+'）</h2><table><tr><th>启用</th><th>名称</th><th>类型</th><th>归类</th><th>操作</th></tr>'
                for f in feeds:
                    kind=f.get("type","x_user")
                    key=f["handle"]
                    body+='<tr><td>'+("是" if f.get("enabled",True) else "否")+'</td><td>'+h(f["name"])+                          '<p class="muted">'+h(f.get("url",key))+'</p></td><td>'+h(kind)+'</td><td>'+h(f.get("group","其他"))+'</td><td>'
                    body+=(
                      '<form action="/sources/toggle" method="post">'+self.input_csrf()+
                      '<input type="hidden" name="handle" value="'+h(key)+'">'+
                      '<button class="secondary">'+("停用" if f.get("enabled",True) else "启用")+'</button></form>'+
                      '<form action="/sources/edit" method="post">'+self.input_csrf()+
                      '<input type="hidden" name="handle" value="'+h(key)+'">'+
                      '<label>名称 <input name="name" value="'+h(f["name"])+'" required maxlength="90"></label>'+
                      '<label>归类 <input name="group" value="'+h(f.get("group","其他"))+'" required maxlength="90"></label>'+
                      '<button class="secondary">保存</button></form>'+
                      '<form action="/sources/delete" method="post">'+self.input_csrf()+
                      '<input type="hidden" name="handle" value="'+h(key)+'">'+
                      '<label><input type="checkbox" name="confirm" value="DELETE" required>确认移除信源</label>'+
                      '<button class="danger">删除</button></form></td></tr>')
                body+='</table></section>'
            elif path=="/groups":
                body='<section><h2>新建推送分组</h2><form action="/groups/new" method="post">'+self.input_csrf()+                     '<label>分组名称 <input name="name" required maxlength="64" placeholder="AI技术情报"></label><button>创建分组</button></form></section>'
                body+='<section><h2>独立推送分组</h2><table><tr><th>名称</th><th>状态</th><th>信源数</th><th>机器人</th><th>操作</th></tr>'
                for g in groups.summary(db):
                    body+='<tr><td>'+h(g["name"])+'</td><td>'+("启用" if g["enabled"] else "停用")+'</td><td>'+str(g["members"])+                          '</td><td>'+("已配置" if g["secret_configured"] else "未配置")+'</td><td><a href="/groups/'+h(g["id"])+'">管理</a></td></tr>'
                body+='</table><p class="muted">系统保留的原 RSS 情报群仍沿用已有配置，不会双重发送。</p></section>'
            elif path.startswith("/groups/"):
                gid=groups.validate_id(path.split("/")[-1])
                group=db.execute("SELECT * FROM admin_groups WHERE id=?",(gid,)).fetchone()
                if group is None:raise ValueError("GROUP_NOT_FOUND")
                members={r[0] for r in db.execute("SELECT handle FROM admin_members WHERE group_id=?",(gid,))}
                body='<section><p><a href="/groups">← 返回分组</a></p><h2>管理分组：'+h(group["name"])+'</h2>'
                body+='<form action="/groups/save" method="post">'+self.input_csrf()+                      '<input type="hidden" name="id" value="'+gid+'">'
                body+='<div class="row"><label>名称 <input name="name" required maxlength="64" value="'+h(group["name"])+'"></label>'+                      '<label>每批最多 <input type="number" name="limit" min="1" max="12" value="'+str(group["batch_limit"])+'"></label>'+                      '<label>最短间隔（秒） <input type="number" name="interval" min="300" max="86400" value="'+str(group["interval_seconds"])+'"></label></div>'
                body+='<p><label><input type="checkbox" name="enabled" value="1"'+(' checked' if group["enabled"] else '')+'> 启用定时发送</label></p>'
                body+='<h3>飞书群机器人</h3><p class="muted">'+('已配置 · 原值不可查看；填写新地址可安全替换' if groups.has_secret(gid,groups.SECRET_DIR) else '尚未配置；启用前必须先保存机器人')+'</p>'
                body+='<label>机器人 Webhook <input type="password" name="webhook" size="75" autocomplete="off" placeholder="留空不修改已保存的机器人"></label>'
                body+='<h3>勾选该群需要推送的信源（可跨群重复选择）</h3><div class="grid">'
                for f in feeds:
                    if not f.get("enabled",True):continue
                    body+='<label class="item"><input type="checkbox" name="source" value="'+h(f["handle"])+'"'+                          (' checked' if f["handle"] in members else '')+'> <b>'+h(f["name"])+'</b><br><span class="muted">'+h(f.get("group","其他"))+'</span></label>'
                body+='</div><p><button>保存分组与信源选择</button></p></form>'
                body+='<hr><form action="/groups/test" method="post">'+self.input_csrf()+                      '<input type="hidden" name="id" value="'+gid+'">'+                      '<label><input type="checkbox" name="confirm" value="yes" required> 确认向此群发送 1 条【测试】消息</label>'+                      '<button class="secondary">测试机器人投递</button></form></section>'
            elif path=="/history":
                body='<section><h2>各群投递记录</h2><table><tr><th>群组</th><th>批次</th><th>状态</th><th>次数</th><th>文章数</th></tr>'
                for x in db.execute("SELECT b.id,b.state,b.attempts,g.name,COUNT(d.article_id) ct "
                                    "FROM admin_batches b JOIN admin_groups g ON g.id=b.group_id "
                                    "LEFT JOIN admin_deliveries d ON d.batch_id=b.id "
                                    "GROUP BY b.id ORDER BY b.id DESC LIMIT 100"):
                    body+='<tr><td>'+h(x["name"])+'</td><td>'+str(x["id"])+'</td><td>'+h(x["state"])+                          '</td><td>'+str(x["attempts"])+'</td><td>'+str(x["ct"])+'</td></tr>'
                body+='</table><p class="muted">不确定投递不会自动重发。原 RSS 群的历史仍由旧数据库批次管理。</p></section>'
            else:raise ValueError("PAGE_NOT_FOUND")
        self.reply(200,self.base("RSS 情报管理",body))

    def do_POST(self):
        if not self.same_origin():
            self.reply(403,"请求来源无效");return
        path=urlsplit(self.path).path
        if path=="/login":
            try:form=self.read_form()
            except (ValueError,UnicodeError):self.reply(400,"表单无效");return
            ip=self.client_address[0]
            attempts=self.server.failures.get(ip,[])
            now=time.time();attempts=[t for t in attempts if now-t<300]
            if len(attempts)>=8:
                self.reply(429,"登录尝试过多");return
            if not hmac.compare_digest(self.val(form,"token").encode(),self.server.token):
                attempts.append(now);self.server.failures[ip]=attempts
                self.reply(403,self.base("登录失败",'<section class="error">凭据错误</section>'));return
            self.server.failures.pop(ip,None)
            sid=session(self.server.token)
            self.redirect("/",{"Set-Cookie":"sid="+sid+"; Path=/; HttpOnly; SameSite=Strict; Max-Age="+str(COOKIE_AGE)})
            return
        if not self.authenticated():
            self.reply(401,"请先登录");return
        try:form=self.read_form()
        except (ValueError,UnicodeError):self.reply(400,"表单无效");return
        if not hmac.compare_digest(self.val(form,"csrf"),csrf_token(self.server.token,self._sid())):
            self.reply(403,"CSRF 验证失败");return
        try:
            if path=="/groups/test":
                gid=groups.validate_id(self.val(form,"id"))
                if self.val(form,"confirm")!="yes":raise ValueError("REQUIRES_EXPLICIT_TEST_CONFIRMATION")
                # Test can be deliberately sent again only by another explicit click.
                with self.db() as db:
                    row=db.execute("SELECT name FROM admin_groups WHERE id=?",(gid,)).fetchone()
                    if row is None:raise ValueError("GROUP_NOT_FOUND")
                    payload=rss.build_post([],int(time.time()),test=True)
                    payload["card"]["header"]["title"]["content"]="RSS情报｜"+rss.safe_card_text(row["name"],20)+"【测试】"
                    try:
                        rss.send_payload(payload,groups.read_secret(gid,groups.SECRET_DIR))
                    except Exception as exc:
                        groups.audit(db,"bot_test_unknown_or_failed",gid)
                        db.commit()
                        raise ValueError("TEST_RESULT_UNCERTAIN_CHECK_FEISHU_CLIENT") from exc
                    groups.audit(db,"bot_test_api_accepted",gid)
                    db.commit()
                self.redirect("/groups/"+gid);return

            # All other mutation paths must hold the shared collector lock.
            LOCK_FILE.parent.mkdir(parents=True,exist_ok=True)
            with LOCK_FILE.open("a+b") as lock:
                try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise ValueError("BUSY_TRY_AFTER_COLLECTOR_FINISHES")
                if path=="/groups/new":
                    with self.db() as db:gid=groups.create_group(db,self.val(form,"name"))
                    self.redirect("/groups/"+gid);return
                if path=="/groups/save":
                    gid=groups.validate_id(self.val(form,"id"))
                    feeds=rss.loadfeeds()
                    chosen=form.get("source",[])
                    allowed=[f["handle"] for f in feeds]
                    # Validate form before any side effect.
                    if not set(chosen).issubset(set(allowed)):
                        raise ValueError("UNKNOWN_SOURCE_SELECTION")
                    hook=self.val(form,"webhook").strip()
                    if hook:rss.validate_webhook_url(hook)
                    with self.db() as db:
                        if hook:groups.store_secret(gid,hook,groups.SECRET_DIR)
                        groups.set_members(db,gid,chosen,allowed)
                        groups.update_group(db,gid,self.val(form,"name"),
                                 self.val(form,"enabled")=="1",
                                 self.val(form,"limit","12"),self.val(form,"interval","3300"),groups.SECRET_DIR)
                    self.redirect("/groups/"+gid);return
                if path in ("/sources/add","/sources/toggle","/sources/edit","/sources/delete"):
                    self.mutate_feeds(path,form)
                    self.redirect("/sources");return
            raise ValueError("UNKNOWN_MUTATION")
        except (ValueError,TypeError) as exc:
            self.fail(str(exc))
        except (OSError,sqlite3.Error):
            self.fail("系统状态写入失败，请检查服务器权限或日志",500)

    def mutate_feeds(self,path,form):
        data=json.loads(rss.FEEDS_FILE.read_text(encoding="utf-8"))
        items=data["feeds"]
        if path=="/sources/add":
            kind=self.val(form,"type")
            name=self.val(form,"name").strip()
            category=self.val(form,"group","其他").strip() or "其他"
            if kind=="x_user":
                item={"handle":self.val(form,"handle").lstrip("@"),"name":name,"group":category}
            elif kind=="rss":
                item={"type":"rss","url":self.val(form,"url").strip(),"name":name,"group":category}
            else:raise ValueError("UNKNOWN_SOURCE_TYPE")
            items.append(item)
            action="source_added"
        else:
            handle=self.val(form,"handle")
            actual=rss.normalize_feeds(data)
            found=next((f for f in actual if f["handle"]==handle),None)
            if found is None:raise ValueError("SOURCE_NOT_FOUND")
            idx=next(i for i,f in enumerate(actual) if f["handle"]==handle)
            if path=="/sources/toggle":
                items[idx]["enabled"]=not found.get("enabled",True)
                action="source_toggled"
            elif path=="/sources/edit":
                items[idx]["name"]=self.val(form,"name").strip()
                items[idx]["group"]=self.val(form,"group").strip()
                action="source_edited"
            else:
                if self.val(form,"confirm")!="DELETE":
                    raise ValueError("DELETE_REQUIRES_CONFIRMATION")
                items.pop(idx)
                action="source_deleted"
        checked=rss.normalize_feeds(data)
        if not any(f.get("enabled",True) for f in checked):
            raise ValueError("AT_LEAST_ONE_ACTIVE_SOURCE_REQUIRED")
        # Optional disabled sources preserved in manifest, not polled.
        encoded=(json.dumps(data,ensure_ascii=False,indent=2)+"\n").encode()
        backup=rss.FEEDS_FILE.parent/"state"/"feed-ui-backups"
        backup.mkdir(mode=0o700,parents=True,exist_ok=True)
        original=rss.FEEDS_FILE.read_bytes()
        name=hashlib.sha256(original).hexdigest()[:18]
        p=backup/(name+".json")
        if not p.exists():
            fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,"wb") as out:out.write(original)
        fd,tmp=tempfile.mkstemp(prefix=".feeds-ui-",dir=rss.FEEDS_FILE.parent)
        try:
            with os.fdopen(fd,"wb") as out:
                out.write(encoded);out.flush();os.fsync(out.fileno())
            os.chmod(tmp,0o640)
            os.replace(tmp,rss.FEEDS_FILE)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        with self.db() as db:
            if action=="source_deleted":
                for g in groups.all_groups(db):
                    remaining=[x[0] for x in db.execute(
                        "SELECT handle FROM admin_members WHERE group_id=? AND handle!=?",
                        (g["id"],handle))]
                    groups.set_members(db,g["id"],remaining,
                        [f["handle"] for f in checked])
            groups.audit(db,action,"catalogue")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",nargs="?",choices=("serve","check"),default="serve")
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    token=get_token()
    if args.command=="check":
        print("ADMIN_AUTH_CONFIGURATION_OK")
        return
    if not 1024<=args.port<=65535:raise SystemExit("INVALID_PORT")
    db=rss.dbopen()
    try:groups.initialize(db)
    finally:db.close()
    server=AppServer(("127.0.0.1",args.port),Handler,token)
    print("RSS_ADMIN_LOOPBACK_BOUND",flush=True)
    server.serve_forever()

if __name__=="__main__":main()
