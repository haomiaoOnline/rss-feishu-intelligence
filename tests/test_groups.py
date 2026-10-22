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
        assert next(g for g in groups.summary(db) if g["id"]==gb)["batch_states"].get("sent")==1
        print("PASS_MEMBERSHIP_INDEPENDENT")
        db.close()
if __name__=="__main__":test()
