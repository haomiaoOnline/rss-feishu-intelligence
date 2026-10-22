"""Offline multi-group Feishu routing tests; never sends an external request."""
import os
import pathlib
import sqlite3
import stat
import sys
import tempfile
import time

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import rss_feishu_push as rss
import rss_groups as groups

def test():
    with tempfile.TemporaryDirectory(prefix="rss-groups-") as tmp:
        path=pathlib.Path(tmp)
        rss.STATE=path/"push.sqlite3"
        store=path/"secrets"
        db=rss.dbopen()
        groups.initialize(db)
        now=int(time.time())
        assert "eligible_for_groups" in {r[1] for r in db.execute("PRAGMA table_info(articles)")}
        # Existing production history must not be interpreted as a fresh group event.
        db.execute("INSERT INTO articles(id,handle,title,url,published,discovered,delivered,"
                   "eligible_for_groups) VALUES(?,?,?,?,?,?,?,?)",
                   ("legacy","dotey","Historical","https://x.com/dotey/status/5",now-7200,now-7200,1,0))
        db.execute("INSERT INTO batches(created,payload,state,attempts) VALUES (?,'{}','sent',1)",(now-3600,))
        db.commit()
        ga=groups.create_group(db,"AI 技术",12,300)
        gb=groups.create_group(db,"副业观察",12,300)
        selection=["dotey"]
        assert groups.set_members(db,ga,selection,["dotey","other"])["added"]==1
        assert groups.set_members(db,gb,selection,["dotey","other"])["added"]==1
        hook_a="https://open.feishu.cn/open-apis/bot/v2/hook/"+"a"*32
        hook_b="https://open.feishu.cn/open-apis/bot/v2/hook/"+"b"*32
        groups.store_secret(ga,hook_a,store)
        groups.store_secret(gb,hook_b,store)
        assert stat.S_IMODE(groups.secret_path(ga,store).stat().st_mode)==0o600
        groups.update_group(db,ga,"AI 技术",True,12,300,store)
        groups.update_group(db,gb,"副业观察",True,12,300,store)
        # First poll for a new feed must not generate group deliveries.
        db.execute("INSERT INTO articles(id,handle,title,url,published,discovered,delivered,"
                   "eligible_for_groups) VALUES(?,?,?,?,?,?,?,?)",
                   ("baseline","dotey","First baseline","https://x.com/dotey/status/9",
                    now+1,now+1,1,0))
        db.execute("INSERT INTO articles(id,handle,title,url,published,discovered,delivered,"
                   "eligible_for_groups) VALUES(?,?,?,?,?,?,?,?)",
                   ("new","dotey","An AI Agent update","https://x.com/dotey/status/10",
                    now+1,now+1,1,1))
        db.commit()
        deliveries=[]
        def mock_send(payload,target):
            deliveries.append((payload,target))
            assert "RSS情报" in payload["card"]["header"]["title"]["content"]
        feeds=[{"handle":"dotey","name":"宝玉","group":"技术"}]
        result=groups.dispatch(db,feeds,mock_send,store)
        assert sorted(x[1] for x in result)==["sent","sent"],result
        assert len(deliveries)==2,deliveries
        assert {url for _,url in deliveries}=={hook_a,hook_b}
        assert all("https://x.com/dotey/status/10" in str(payload) for payload,_ in deliveries)
        assert all("https://x.com/dotey/status/9" not in str(payload) for payload,_ in deliveries)
        assert db.execute("SELECT count(*) FROM admin_deliveries").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM batches WHERE state='sent'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM articles WHERE id='legacy' AND delivered=1").fetchone()[0]==1
        assert len(groups.dispatch(db,feeds,mock_send,store))==2
        assert len(deliveries)==2
        print("PASS_MULTIGROUP_INDEPENDENT_WEBHOOK_NO_DUPLICATE_LEGACY_PRESERVED")
        # URL formats: retain HTTPS host/path allowlist and reject unrelated
        # Feishu app, chat and legacy custom-bot endpoints.
        prefix="https://open.feishu.cn/open-apis/bot/v2/hook/"
        short_hook=prefix+"f"*16
        assert rss.validate_webhook_url("  <"+short_hook+"/>  ")==short_hook
        assert rss.validate_webhook_url('  "'+short_hook+'/"  ')==short_hook
        for invalid in (
            "https://open.feishu.cn/open-apis/bot/hook/"+"f"*32,
            "http://open.feishu.cn/open-apis/bot/v2/hook/"+"f"*32,
            "https://evil.example/open-apis/bot/v2/hook/"+"f"*32,
            "https://open.feishu.cn/open-apis/bot/v2/hook/",
            "https://open.feishu.cn/open-apis/bot/v2/hook/"+"f"*32+"?key=test",
            "https://open.feishu.cn/open-apis/bot/v2/hook/"+"f"*32+"@evil.invalid",
        ):
            try:rss.validate_webhook_url(invalid)
            except ValueError as exc:
                assert "f"*32 not in str(exc) and "test" not in str(exc)
            else:raise AssertionError("unsafe bot URL accepted")
        print("PASS_WEBHOOK_NORMALIZATION_STRICT_HOST_AND_ACTIONABLE_ERRORS")
        # Provider diagnostics must not disclose URLs, secrets or raw data.
        assert rss.safe_provider_message("message type not supported")=="message type not supported"
        assert rss.safe_provider_message("secret: https://open.feishu.cn/open-apis/bot/v2/hook/"+"a"*32)==""
        assert rss.safe_provider_message("token=supersecret")==""
        assert rss.safe_provider_message("<script>alert(1)</script>")==""
        assert rss.safe_provider_message("unexpected_very_long_secret_"+"a"*30)==""
        assert rss.safe_provider_message("消息类型不支持")=="消息类型不支持"
        assert rss.KnownFailure("PROVIDER_CODE_19007","message type not supported").public_message=="message type not supported"
        from unittest.mock import patch
        import json
        class FakeRejectedResponse:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return None
            def read(self,max_bytes):
                return json.dumps({"code":19007,"msg":"message type not supported"}).encode()
        with patch.object(rss.urllib.request,"urlopen",return_value=FakeRejectedResponse()):
            try:
                rss.send_payload({"msg_type":"text","content":{"text":"RSS情报｜联通测试"}},
                                 hook_a)
            except rss.KnownFailure as exc:
                assert str(exc)=="PROVIDER_CODE_19007"
                assert exc.public_message=="message type not supported"
            else:
                raise AssertionError("rejected provider response treated as accepted")
        print("PASS_PROVIDER_MSG_SANITIZATION_AND_ERROR_19007")
        # Feishu signature is HMAC-SHA256 over empty message, keyed by
        # timestamp + newline + sign secret. Signature key is never in payload.
        import base64,hashlib,hmac
        sign_key="synthetic-sign-key-for-unit-test"
        timestamp=1792660000
        signed=rss.signed_bot_payload({"msg_type":"interactive","card":{}},
                                      sign_key,timestamp=timestamp)
        expected=base64.b64encode(hmac.new(
            (str(timestamp)+chr(10)+sign_key).encode(),b"",
            hashlib.sha256).digest()).decode()
        assert signed["timestamp"]==str(timestamp)
        assert signed["sign"]==expected
        assert sign_key not in str(signed)
        assert "timestamp" not in {"msg_type":"interactive","card":{}}
        groups.store_sign_secret(gb,sign_key,store)
        assert groups.has_sign_secret(gb,store)
        assert groups.read_sign_secret(gb,store)==sign_key
        assert stat.S_IMODE(groups.sign_path(gb,store).stat().st_mode)==0o600
        group_send=[]
        def signed_mock(payload,target,*,sign_secret=None):
            group_send.append((target,sign_secret))
            return True
        db.execute("UPDATE admin_groups SET last_sent=0 WHERE id=?",(gb,))
        db.execute("INSERT INTO articles(id,handle,title,url,published,discovered,delivered,"
                   "eligible_for_groups) VALUES(?,?,?,?,?,?,?,1)",
                   ("signed-new","dotey","Signed delivery item",
                    "https://x.com/dotey/status/991",now+3,now+3,1))
        db.commit()
        groups.dispatch(db,feeds,signed_mock,store)
        assert (hook_b,sign_key) in group_send
        print("PASS_PRIVATE_FEISHU_SIGNING_AND_PER_GROUP_DISPATCH")
        # Missing webhook is a hard enablement error.
        gc=groups.create_group(db,"未配置",12,300)
        groups.set_members(db,gc,["dotey"],["dotey"])
        try:groups.update_group(db,gc,"未配置",True,12,300,store)
        except ValueError as exc:assert "WEBHOOK" in str(exc)
        else:raise AssertionError("enabled group without hook")
        assert not groups.has_secret(gc,store)
        print("PASS_MISSING_WEBHOOK_REJECTED")
        # Membership changes are scoped to one group.
        groups.set_members(db,ga,[],["dotey"])
        assert db.execute("SELECT count(*) FROM admin_members WHERE group_id=?",(ga,)).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM admin_members WHERE group_id=?",(gb,)).fetchone()[0]==1
        assert next(g for g in groups.summary(db) if g["id"]==gb)["batch_states"].get("sent",0)>=1
        print("PASS_MEMBERSHIP_INDEPENDENT")
        # Removing one subscribed source from a mixed pending batch must NOT
        # permanently lose unrelated queued articles in that same batch.
        gd=groups.create_group(db,"混合来源回归",12,300)
        groups.set_members(db,gd,["dotey","other"],["dotey","other"])
        groups.store_secret(gd,hook_a,store)
        groups.update_group(db,gd,"混合来源回归",True,12,300,store)
        for i,handle in enumerate(("dotey","other")):
            db.execute("INSERT INTO articles(id,handle,title,url,published,discovered,"
                       "delivered,eligible_for_groups) VALUES (?,?,?,?,?,?,?,1)",
                       ("unbatched-"+str(i),handle,"New AI Agent post",
                        "https://x.com/"+handle+"/status/"+str(21+i),
                        now+2,now+2,1))
        db.commit()
        current=db.execute("SELECT * FROM admin_groups WHERE id=?",(gd,)).fetchone()
        initial=groups._make_batch(db,current,[{"handle":"dotey","name":"宝玉"},
                 {"handle":"other","name":"Other"}],now+2)
        assert initial is not None
        assert {r[0] for r in db.execute("SELECT article_id FROM admin_deliveries WHERE batch_id=?",
                    (initial["id"],))}.issuperset({"unbatched-0","unbatched-1"})
        groups.set_members(db,gd,["dotey"],["dotey","other"])
        assert db.execute("SELECT state FROM admin_batches WHERE id=?",
                          (initial["id"],)).fetchone()[0]=="cancelled"
        assert db.execute("SELECT count(*) FROM admin_deliveries WHERE batch_id=?",
                          (initial["id"],)).fetchone()[0]==0
        again=groups._make_batch(db,current,[{"handle":"dotey","name":"宝玉"}],now+2)
        assert again is not None and again["id"]!=initial["id"]
        newids={r[0] for r in db.execute("SELECT article_id FROM admin_deliveries WHERE batch_id=?",
                          (again["id"],))}
        assert "unbatched-0" in newids and "unbatched-1" not in newids
        groups.archive_group(db,gd,"ARCHIVE")
        assert not any(g["id"]==gd for g in groups.summary(db))
        assert db.execute("SELECT archived FROM admin_groups WHERE id=?",
                          (gd,)).fetchone()[0]==1
        assert db.execute("SELECT state FROM admin_batches WHERE id=?",
                          (again["id"],)).fetchone()[0]=="cancelled"
        print("PASS_PENDING_MIXED_BATCH_REQUEUE_AND_ARCHIVE_PRESERVES_HISTORY")
        db.close()
if __name__=="__main__":test()
