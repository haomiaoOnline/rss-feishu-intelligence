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
import re
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

    def local_host_allowed(self):
        """Only accept the exact loopback Host on this bound port.

        Browser extensions, privacy tooling and embedded browsers can alter
        the Origin field even for a form opened on localhost. Origin is not
        the authority for a localhost-only admin app. Login requires the
        private admin token; authenticated writes require an HMAC-bound CSRF
        token plus the HttpOnly SameSite session cookie.
        """
        port=self.server.server_port
        return self.headers.get("Host","") in (
            "127.0.0.1:"+str(port),"localhost:"+str(port))

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
        if not self.local_host_allowed():
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
                term=parse_qs(urlsplit(self.path).query).get("q",[""])[0].strip()[:80]
                body='<section><h2>新增信源</h2><form action="/sources/add" method="post">'+self.input_csrf()
                body+='<label>类型 <select name="type"><option value="x_user">X 博主</option><option value="rss">通用 HTTPS RSS</option></select></label>'
                for name,label,placeholder in (("handle","X 用户名","dotey"),("url","RSS URL","https://example.org/rss.xml"),
                    ("name","显示名称","作者/网站名称"),("group","归类","AI / 增长")):
                    body+='<label>'+label+' <input name="'+name+'" placeholder="'+placeholder+'"></label>'
                body+='<button>添加信源</button></form></section>'
                shown=[f for f in feeds if not term or term.lower() in
                       (f["name"]+" "+f["handle"]+" "+f.get("group","")+" "+f.get("url","")).lower()]
                body+='<section><h2>全部信源（'+str(len(feeds))+'）</h2>'+(
                    '<form method="get" action="/sources"><label>搜索信源 <input name="q" value="'+h(term)+'" placeholder="作者、账号、类别"></label><button>筛选</button></form>')+(
                    '<p class="muted">显示 '+str(len(shown))+' 个匹配信源</p>')+(
                    '<table><tr><th>启用</th><th>名称</th><th>类型</th><th>归类</th><th>操作</th></tr>')
                for f in shown:
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
                if group is None or group["archived"]:raise ValueError("GROUP_NOT_FOUND")
                members={r[0] for r in db.execute("SELECT handle FROM admin_members WHERE group_id=?",(gid,))}
                body='<section><p><a href="/groups">← 返回分组</a></p><h2>管理分组：'+h(group["name"])+'</h2>'
                body+='<form action="/groups/save" method="post">'+self.input_csrf()+                      '<input type="hidden" name="id" value="'+gid+'">'
                body+='<div class="row"><label>名称 <input name="name" required maxlength="64" value="'+h(group["name"])+'"></label>'+                      '<label>每批最多 <input type="number" name="limit" min="1" max="12" value="'+str(group["batch_limit"])+'"></label>'+                      '<label>最短间隔（秒） <input type="number" name="interval" min="300" max="86400" value="'+str(group["interval_seconds"])+'"></label></div>'
                body+='<p><label><input type="checkbox" name="enabled" value="1"'+(' checked' if group["enabled"] else '')+'> 启用定时发送</label></p>'
                body+='<h3>飞书群机器人</h3><p class="muted">'+('已配置 · 原值不可查看；填写新地址可安全替换' if groups.has_secret(gid,groups.SECRET_DIR) else '尚未配置；启用前必须先保存机器人')+'</p>'
                body+='<p class="muted">这里填写的是<strong>飞书群设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook 地址</strong>，不是飞书应用 App ID、群聊链接或智能体工作流回调地址。格式如 https://open.feishu.cn/open-apis/bot/v2/hook/你的机器人标识；不必把地址发到聊天中。</p>'
                body+='<label>群自定义机器人 Webhook <input type="password" name="webhook" size="75" autocomplete="off" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." ></label>'
                body+='<p class="muted">如机器人开启「签名校验」，请填写签名密钥；只用关键词/IP白名单则留空。</p>'
                body+='<label>可选签名密钥 <input type="password" name="sign_secret" size="55" autocomplete="off" placeholder="留空不修改原签名密钥"></label>'
                body+='<p class="muted">'+("签名密钥已配置" if groups.has_sign_secret(gid,groups.SECRET_DIR) else "未配置签名密钥")+'</p>'
                body+='<h3>勾选该群需要推送的信源（可跨群重复选择）</h3><div class="grid">'
                for f in feeds:
                    if not f.get("enabled",True):continue
                    body+='<label class="item"><input type="checkbox" name="source" value="'+h(f["handle"])+'"'+                          (' checked' if f["handle"] in members else '')+'> <b>'+h(f["name"])+'</b><br><span class="muted">'+h(f.get("group","其他"))+'</span></label>'
                body+='</div><p><button>保存分组与信源选择</button></p></form>'
                latest_test=db.execute(
                    "SELECT action,happened FROM admin_audit "
                    "WHERE subject=? AND action LIKE 'bot_test_%' ORDER BY id DESC LIMIT 1",
                    (gid,)).fetchone()
                if latest_test:
                    action=latest_test["action"]
                    if action=="bot_test_api_accepted":
                        feedback="最近一次测试：飞书接口已接受，请到目标群确认实际消息。"
                    elif action.startswith("bot_test_rejected_"):
                        feedback="最近一次测试："+self.bot_test_explanation(
                            "rejected",action[len("bot_test_rejected_"):])
                    elif action.startswith("bot_test_config_"):
                        feedback="最近一次测试："+self.bot_test_explanation("config")
                    else:
                        feedback="最近一次测试："+self.bot_test_explanation("uncertain")
                    body+='<p class="muted">'+h(feedback)+'</p>'
                if parse_qs(urlsplit(self.path).query).get("test")==["accepted"]:
                    body+='<div class="ok">飞书接口已接受测试消息。请到目标群确认实际收到，不代表客户端已经验收。</div>'
                body+='<form action="/groups/archive" method="post">'+self.input_csrf()+(
                      '<input type="hidden" name="id" value="'+gid+'">'+
                      '<label><input type="checkbox" name="confirm" value="ARCHIVE" required>确定归档并停用这个分组（保留历史）</label>'+
                      '<button class="danger">归档分组</button></form>')
                body+='<hr><form action="/groups/test" method="post">'+self.input_csrf()+(
                      '<input type="hidden" name="id" value="'+gid+'">'+
                      '<label>测试类型 <select name="test_mode">'+
                      '<option value="text">纯文本连通性测试（建议先选）</option>'+
                      '<option value="card">原有交互卡片测试</option></select></label>'+
                      '<p class="muted">两种方式都会真实发出一条消息；请先确认目标群未收到上一次测试，再执行一次。文本成功、卡片失败说明可能与卡片格式有关。</p>'+
                      '<label><input type="checkbox" name="confirm" value="yes" required> 确认向此群发送 1 条【测试】消息</label>'+
                      '<button class="secondary">发送一次测试</button></form></section>')
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

    @staticmethod
    def bot_test_explanation(kind,code=""):
        """User-facing reason without returning URL, credentials or API response body."""
        advice={
            "19001":"机器人 Webhook 无效、已撤销或复制不完整；请在目标飞书群重新复制自定义机器人地址。",
            "19021":"机器人签名校验失败，请检查分组中保存的签名密钥以及云服务器时间。",
            "19022":"机器人启用了 IP 白名单，当前云服务器的出口 IP 未被允许；请检查飞书机器人安全设置。",
            "19024":"机器人安全关键词不匹配；请在飞书群机器人中允许“RSS情报”，或调整关键词。",
            "19002":"飞书拒绝消息格式，请检查卡片结构与机器人支持能力。",
            "19036":"飞书拒绝消息大小，请缩小消息内容。",
            "11232":"飞书机器人触发频率限制；请不要连续点击测试。",
        }
        if kind=="rejected":
            raw=str(code)
            if raw.startswith("PROVIDER_CODE_"):
                numeric=raw[len("PROVIDER_CODE_"):]
                if not numeric.isdigit():
                    return "飞书接口明确返回失败；详情已被安全过滤。"
                return "飞书拒绝投递（错误码 "+numeric+"）。"+advice.get(
                    numeric,("飞书公开自定义机器人文档未明确说明此错误码。"
                             "请查看飞书返回的安全错误描述；建议先用纯文本测试区分卡片格式问题。")
                             if numeric=="19007" else
                             "请检查目标群机器人权限、安全策略和配置。")
            if raw.startswith("HTTP_") and raw[len("HTTP_"):].isdigit():
                return "飞书接口返回 HTTP "+raw[len("HTTP_"):]+"；未确认送达，请检查服务器网络和机器人状态。"
            return "机器人消息未获接口接受，请检查消息格式和安全关键词。"
        if kind=="uncertain":
            return ("网络连接超时或响应异常，消息可能已送达。请先检查目标飞书群；"
                    "不要立即反复测试，避免重复消息。")
        if kind=="config":
            return "机器人配置文件不可用或权限不正确，尚未开始发送。请检查机器人地址是否已保存。"
        return "机器人测试发生未识别异常，不能确认是否送达。请先检查飞书群，避免重复推送。"

    def do_POST(self):
        if not self.local_host_allowed():
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
                if self.val(form,"confirm")!="yes":
                    raise ValueError("REQUIRES_EXPLICIT_TEST_CONFIRMATION")
                with self.db() as db:
                    row=db.execute("SELECT name,archived FROM admin_groups WHERE id=?",(gid,)).fetchone()
                    if row is None or row["archived"]:
                        raise ValueError("GROUP_NOT_FOUND")
                    # Fail early on secret-file errors: no outbound message sent.
                    try:
                        hook=groups.read_secret(gid,groups.SECRET_DIR)
                    except (ValueError,OSError):
                        groups.audit(db,"bot_test_config_invalid",gid)
                        db.commit()
                        self.fail(self.bot_test_explanation("config"));return
                    mode=self.val(form,"test_mode","card")
                    if mode not in ("text","card"):
                        raise ValueError("INVALID_TEST_MODE")
                    if mode=="text":
                        payload={"msg_type":"text","content":{"text":
                            "RSS情报｜"+rss.safe_card_text(row["name"],20)+"｜文本连通性【测试】"}}
                    else:
                        payload=rss.build_post([],int(time.time()),test=True)
                        payload["card"]["header"]["title"]["content"]="RSS情报｜"+rss.safe_card_text(row["name"],20)+"【测试】"
                    try:
                        if groups.has_sign_secret(gid,groups.SECRET_DIR):
                            signkey=groups.read_sign_secret(gid,groups.SECRET_DIR)
                            rss.send_payload(payload,hook,sign_secret=signkey)
                        else:
                            rss.send_payload(payload,hook)
                    except rss.KnownFailure as exc:
                        raw=str(exc)
                        code=raw if re.fullmatch(r"[A-Z_0-9]{1,40}",raw) else "UNKNOWN"
                        groups.audit(db,"bot_test_rejected_"+code,gid)
                        db.commit()
                        message=self.bot_test_explanation("rejected",code)
                        safe=getattr(exc,"public_message","")
                        if safe:
                            message+=" 飞书返回："+safe
                        self.fail(message);return
                    except rss.UncertainFailure as exc:
                        kind=type(exc).__name__
                        groups.audit(db,"bot_test_uncertain_"+kind,gid)
                        db.commit()
                        self.fail(self.bot_test_explanation("uncertain"));return
                    except Exception:
                        groups.audit(db,"bot_test_uncertain_unknown",gid)
                        db.commit()
                        self.fail(self.bot_test_explanation("unknown"));return
                    groups.audit(db,"bot_test_api_accepted",gid)
                    db.commit()
                self.redirect("/groups/"+gid+"?test=accepted");return

            # All other mutation paths must hold the shared collector lock.
            LOCK_FILE.parent.mkdir(parents=True,exist_ok=True)
            with LOCK_FILE.open("a+b") as lock:
                try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise ValueError("BUSY_TRY_AFTER_COLLECTOR_FINISHES")
                if path=="/groups/archive":
                    gid=groups.validate_id(self.val(form,"id"))
                    with self.db() as db:
                        groups.archive_group(db,gid,self.val(form,"confirm"))
                    self.redirect("/groups");return
                if path=="/groups/new":
                    with self.db() as db:gid=groups.create_group(db,self.val(form,"name"))
                    self.redirect("/groups/"+gid);return
                if path=="/groups/save":
                    gid=groups.validate_id(self.val(form,"id"))
                    feeds=rss.loadfeeds()
                    chosen=form.get("source",[])
                    allowed=[f["handle"] for f in feeds]
                    hook=self.val(form,"webhook").strip()
                    with self.db() as db:
                        groups.save_group_settings(
                            db,gid,self.val(form,"name"),
                            self.val(form,"enabled")=="1",
                            self.val(form,"limit","12"),self.val(form,"interval","3300"),
                            chosen,allowed,hook,groups.SECRET_DIR,
                            sign_secret=self.val(form,"sign_secret"))
                    self.redirect("/groups/"+gid);return
                if path in ("/sources/add","/sources/toggle","/sources/edit","/sources/delete"):
                    self.mutate_feeds(path,form)
                    self.redirect("/sources");return
            raise ValueError("UNKNOWN_MUTATION")
        except (ValueError,TypeError) as exc:
            error=str(exc)
            guidance={
                "WEBHOOK_URL_REQUIRED":"请填写完整的飞书群自定义机器人 Webhook 地址。",
                "WEBHOOK_MUST_BE_HTTPS":"机器人地址必须以 https:// 开头。",
                "WEBHOOK_NOT_FEISHU_GROUP_BOT":"这不是受支持的飞书群自定义机器人 Webhook。请从目标飞书群的「设置 → 群机器人 → 自定义机器人」中复制地址。",
                "WEBHOOK_REQUIRES_V2_CUSTOM_BOT":"需要飞书 v2 群自定义机器人地址（/open-apis/bot/v2/hook/），不是群链接、App ID、旧版 v1 机器人或其他智能体回调地址。",
                "WEBHOOK_ROBOT_ID_INVALID":"飞书机器人标识缺失或格式异常，请复制完整 Webhook，不要只复制机器人名称或 ID。",
                "WEBHOOK_URL_FORMAT_INVALID":"机器人 URL 含有不支持的参数或格式，请直接复制群自定义机器人的完整地址。",
                "GROUP_NEEDS_SOURCES":"请先勾选此分组要推送的信源。",
                "GROUP_WEBHOOK_NOT_CONFIGURED":"请先为这个分组配置飞书群自定义机器人 Webhook。",
                "FEISHU_SIGN_SECRET_INVALID":"签名密钥格式异常，请从机器人安全设置完整复制；不能包含换行。",
            }
            self.fail(guidance.get(error,error))
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
