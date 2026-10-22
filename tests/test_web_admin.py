"""Local HTTP integration: login, CSRF, group/source forms, webhooks redacted."""
import http.client
import json
import pathlib
import re
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlencode

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import rss_feishu_push as rss
import rss_groups as groups
import rss_web_admin as web

def run():
    with tempfile.TemporaryDirectory(prefix="rss-web-integration-") as tmp:
        path=pathlib.Path(tmp)
        rss.STATE=path/"runtime.sqlite3"
        rss.FEEDS_FILE=path/"feeds.json"
        rss.FEEDS_FILE.write_text(json.dumps({"feeds":[
            {"handle":"dotey","name":"宝玉","group":"AI"},
            {"handle":"other","name":"其他作者","group":"AI"}]},ensure_ascii=False))
        groups.SECRET_DIR=path/"webhooks"
        web.LOCK_FILE=path/"collector.lock"
        admin_secret="synthetic-admin-test-token-only-"+"z"*35
        server=web.AppServer(("127.0.0.1",0),web.Handler,admin_secret.encode())
        port=server.server_address[1]
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        cookie=""
        def request(method,uri,values=None,csrf="",headers=None):
            c=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
            body=(urlencode(values or {},doseq=True) if values is not None else None)
            h={"Origin":"http://127.0.0.1:"+str(port)}
            if cookie:h["Cookie"]=cookie
            if method=="POST":h["Content-Type"]="application/x-www-form-urlencoded"
            if headers:h.update(headers)
            c.request(method,uri,body=body,headers=h)
            resp=c.getresponse()
            out=(resp.status,dict(resp.getheaders()),resp.read().decode())
            c.close();return out
        try:
            assert request("GET","/")[0]==303
            assert request("POST","/groups/new",{"name":"Denied"})[0]==401
            code,heads,body=request("GET","/login")
            assert code==200 and "管理员登录" in body
            code,heads,body=request("POST","/login",{"token":"wrong"})
            assert code==403
            code,heads,body=request("POST","/login",{"token":admin_secret})
            assert code==303
            cookie=heads["Set-Cookie"].split(";",1)[0]
            assert "HttpOnly" in heads["Set-Cookie"] and "SameSite=Strict" in heads["Set-Cookie"]
            code,heads,page=request("GET","/")
            assert code==200 and "平台概览" in page and "2" in page
            token=re.search(r'name="csrf" value="([0-9a-f]+)"',request("GET","/groups")[2]).group(1)
            assert request("POST","/groups/new",{"name":"AI技术","csrf":"bad"})[0]==403
            code,heads,_=request("POST","/groups/new",{"name":"AI技术","csrf":token})
            assert code==303 and heads["Location"].startswith("/groups/")
            gid=heads["Location"].split("/")[-1]
            assert re.fullmatch(r"[0-9a-f]{12}",gid)
            # A format/hostname error must not partially persist memberships,
            # group settings or secret files.
            wrong="https://www.example.com/open-apis/bot/v2/hook/"+"x"*32
            code,_,error=request("POST","/groups/save",{
                 "id":gid,"name":"MUST_NOT_PERSIST","enabled":"1","source":["dotey"],
                 "limit":"8","interval":"600","webhook":wrong,"csrf":token})
            assert code==400 and "飞书群自定义机器人" in error and wrong not in error
            with groups.sqlite3.connect(rss.STATE) as check:
                assert check.execute("SELECT name,enabled FROM admin_groups WHERE id=?",(gid,)).fetchone()==("AI技术",0)
                assert check.execute("SELECT count(*) FROM admin_members WHERE group_id=?",(gid,)).fetchone()[0]==0
            assert not groups.has_secret(gid,groups.SECRET_DIR)
            # Previously rejected by the arbitrary 20-character minimum.
            # Pasted quotes/spaces/trailing slash are normalized.
            hook="https://open.feishu.cn/open-apis/bot/v2/hook/"+"d"*18
            pasted='  "'+hook+'/"  '
            code,heads,_=request("POST","/groups/save",{
                 "id":gid,"name":"AI技术","enabled":"1","source":["dotey"],
                 "limit":"8","interval":"600","webhook":pasted,"csrf":token})
            assert code==303
            code,heads,page=request("GET",f"/groups/{gid}")
            assert code==200 and "AI技术" in page and "已配置" in page
            assert "sign_secret" in page
            synthetic_sign="local-browser-sign-key-do-not-use"
            code,_,_=request("POST","/groups/save",{
                 "id":gid,"name":"AI技术","enabled":"1","source":["dotey"],
                 "limit":"8","interval":"600","sign_secret":synthetic_sign,"csrf":token})
            assert code==303
            assert groups.read_sign_secret(gid,groups.SECRET_DIR)==synthetic_sign
            assert synthetic_sign not in request("GET",f"/groups/{gid}")[2]
            assert hook not in page
            assert "dotey" in page
            sent=[]
            def fake(payload,target):
                sent.append((payload,target))
                return True
            def fake_signed(payload,target,*,sign_secret=None):
                assert sign_secret==synthetic_sign
                return fake(payload,target)
            with patch.object(rss,"send_payload",fake_signed):
                code,_,_=request("POST","/groups/test",{"id":gid,"confirm":"yes","csrf":token})
            assert code==303 and len(sent)==1 and sent[0][1]==hook
            assert "【测试】" in sent[0][0]["card"]["header"]["title"]["content"]
            # The API's definite rejection code must be surfaced without
            # revealing the webhook, response body or other credentials.
            for provider_code, hint in [
                ("PROVIDER_CODE_19024","关键词"),
                ("PROVIDER_CODE_19022","IP 白名单"),
                ("PROVIDER_CODE_19021","签名"),
                ("PROVIDER_CODE_19001","Webhook"),
                ("HTTP_403","HTTP 403"),
            ]:
                def reject(payload,target,*,sign_secret=None):
                    raise rss.KnownFailure(provider_code)
                with patch.object(rss,"send_payload",reject):
                    code,_,body=request("POST","/groups/test",{
                        "id":gid,"confirm":"yes","csrf":token})
                assert code==400 and hint in body and hook not in body
            def unknown(payload,target,*,sign_secret=None):
                raise rss.UncertainFailure("TimeoutError")
            with patch.object(rss,"send_payload",unknown):
                code,_,body=request("POST","/groups/test",{
                    "id":gid,"confirm":"yes","csrf":token})
            assert code==400 and "可能已送达" in body and hook not in body
            # Configuration failure must not attempt sending.
            with patch.object(groups,"read_secret",side_effect=ValueError("SECRET_FILE_MUST_BE_PRIVATE_0600")):
                with patch.object(rss,"send_payload",side_effect=AssertionError("unexpected network")):
                    code,_,body=request("POST","/groups/test",{
                        "id":gid,"confirm":"yes","csrf":token})
            assert code==400 and "尚未开始发送" in body
            with patch.object(rss,"send_payload",fake_signed):
                code,_,_=request("POST","/groups/test",{"id":gid,"csrf":token})
            assert code==400 and len(sent)==1
            code,heads,page=request("GET","/history")
            assert code==200 and "投递记录" in page and hook not in page
            code,_,_=request("POST","/sources/add",{
                "type":"rss","url":"https://www.example.org/feed.xml",
                "name":"测试博客","group":"增长","csrf":token})
            assert code==303
            assert len(json.loads(rss.FEEDS_FILE.read_text())["feeds"])==3
            code,_,page=request("GET","/sources")
            assert code==200 and "测试博客" in page and hook not in page
            assert request("POST","/sources/add",{
                "type":"rss","url":"http://127.0.0.1/private",
                "name":"Bad","group":"test","csrf":token})[0]==400
            assert len(json.loads(rss.FEEDS_FILE.read_text())["feeds"])==3
            assert request("POST","/sources/toggle",{
                "handle":"dotey","csrf":token})[0]==303
            assert json.loads(rss.FEEDS_FILE.read_text())["feeds"][0]["enabled"] is False
            assert request("GET","/sources",headers={"Host":"evil.example"})[0]==400
            # Origin can be null/extension/custom in an embedded local browser.
            # The bound Host must remain loopback; login still needs the
            # private admin secret, and every authenticated mutation still
            # needs its own session-bound CSRF token.
            assert request("POST","/login",{"token":admin_secret},
                           headers={"Origin":"http://localhost:"+str(port)})[0]==303
            assert request("POST","/login",{"token":admin_secret},
                           headers={"Origin":"http://localhost:"+str(port)+"/"})[0]==303
            assert request("POST","/login",{"token":admin_secret},
                           headers={"Origin":"http://localhost:"+(str(port+1))})[0]==303
            assert request("POST","/login",{"token":admin_secret},
                           headers={"Origin":"https://example.org"})[0]==303
            assert request("POST","/login",{"token":admin_secret},
                           headers={"Origin":"null"})[0]==303
            assert request("POST","/login",{"token":admin_secret},
                           headers={"Origin":"chrome-extension://example"})[0]==303
            # A forged mutation still fails even with a valid login cookie.
            assert request("POST","/groups/new",{"name":"Forbidden"},
                           headers={"Origin":"https://example.org"})[0]==403
            assert "Forbidden" not in request("GET","/groups")[2]
            assert request("POST","/login",{"token":admin_secret},
                           headers={"Host":"evil.invalid","Origin":"http://localhost:"+str(port)})[0]==403
            code,_,filtered=request("GET","/sources?q=other")
            assert code==200 and "显示 1 个匹配信源" in filtered
            assert "搜索信源" in filtered
            code,_,_=request("POST","/sources/edit",{
                "handle":"other","name":"<img src=x onerror=alert(1)>","group":"改名","csrf":token})
            assert code==303
            code,_,page=request("GET","/sources")
            assert code==200 and "&lt;img" in page and "<img src=x" not in page
            # Delete requires an explicit checkbox, and cannot remove all sources.
            assert request("POST","/sources/delete",{"handle":"other","csrf":token})[0]==400
            code,_,_=request("POST","/sources/delete",{
                "handle":"other","confirm":"DELETE","csrf":token})
            assert code==303
            assert len(json.loads(rss.FEEDS_FILE.read_text())["feeds"])==2
            print("PASS_HTTP_SOURCE_EDIT_HTML_ESCAPE_DELETE_CONFIRMATION")
            code,_,_=request("POST","/groups/archive",{"id":gid,"csrf":token})
            assert code==400
            code,_,_=request("POST","/groups/archive",{
                "id":gid,"csrf":token,"confirm":"ARCHIVE"})
            assert code==303
            assert request("GET",f"/groups/{gid}")[0]==404
            code,_,page=request("GET","/groups")
            assert code==200 and ('href="/groups/'+gid+'"') not in page
            print("PASS_GROUP_ARCHIVE_AND_SERVER_SEARCH")
            print("PASS_HTTP_LOGIN_AUTH_CSRF_LOCALHOST_AND_SAFE_SOURCE_FORMS")
            print("PASS_TWO_SCREENS_GROUP_EDIT_WEBHOOK_REDACTION_AND_TEST_SEND")
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)

if __name__=="__main__":run()
