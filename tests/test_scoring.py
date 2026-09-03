"""Verify scoring math with synthetic data — no API calls."""
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/test_angle.db"
import importlib, config; importlib.reload(config)
import db; importlib.reload(db)
if os.path.exists("/tmp/test_angle.db"): os.remove("/tmp/test_angle.db")
db.init()
from pipeline import score

ACC = "testco"
with db.connect() as conn:
    # signal: (source, kind, text, reach, saves, shares, conv, boosted)
    rows = [
        ("meta_ig","post","morning routine post",1000,80,10,None,0),
        # Save rate 0.90 — by far the highest in the account. If the
        # boosted filter ever stops working, this row's cluster jumps to
        # the top of the save weighting and the assertion below fires.
        # A boosted post with unremarkable numbers would prove nothing.
        ("meta_ig","post","boosted big reach post",50000,45000,5,None,1),
        ("meta_ig","comment","worried about sizing",None,None,None,None,0),
        ("meta_page","comment","does it fit tall people",None,None,None,None,0),
        ("transcript","comment","I wasn't sure it would fit me",None,None,None,None,0),
        ("gsc","query","sizing guide",None,None,None,None,0),
        ("meta_ig","post","share-heavy identity post",2000,10,200,None,0),
        ("ga4","page","sizing help page",None,None,None,40,0),
    ]
    ids=[]
    for i,(src,kind,text,reach,saves,shares,conv,b) in enumerate(rows):
        ids.append(db.insert_signal(conn,ACC,src,kind,text,external_id=f"x{i}",
            reach=reach,saves=saves,shares=shares,conversions=conv,is_boosted=b))
    # clusters
    conn.execute("INSERT INTO cluster (account,canonical,claim_type) VALUES (?,?,?)",
                 (ACC,"I'm worried it won't fit me","objection"))
    conn.execute("INSERT INTO cluster (account,canonical,claim_type) VALUES (?,?,?)",
                 (ACC,"This makes my mornings easier","benefit"))
    conn.execute("INSERT INTO cluster (account,canonical,claim_type) VALUES (?,?,?)",
                 (ACC,"This says something about who I am","identity"))
    # Its own cluster, holding nothing but the boosted post, so the
    # boosted-exclusion assertion has something unambiguous to read.
    conn.execute("INSERT INTO cluster (account,canonical,claim_type) VALUES (?,?,?)",
                 (ACC,"Boosted post, paid reach","benefit"))
    conn.commit()
    c1,c2,c3,c4 = [r["id"] for r in conn.execute("SELECT id FROM cluster ORDER BY id")]
    # claims: fit objection spans 4 sources; morning benefit 1; identity 1
    claims = [
        (ids[2],c1,"objection","worried about sizing"),
        (ids[3],c1,"objection","does it fit tall people"),
        (ids[4],c1,"objection","I wasn't sure it would fit me"),
        (ids[5],c1,"objection","sizing guide"),
        (ids[7],c1,"objection","sizing help page"),
        (ids[0],c2,"benefit","morning routine"),
        (ids[1],c4,"benefit","boosted post text"),
        (ids[6],c3,"identity","identity post"),
    ]
    for sid,cid,ct,txt in claims:
        conn.execute("INSERT INTO claim (account,signal_id,text,verbatim,claim_type,cluster_id) VALUES (?,?,?,?,?,?)",
                     (ACC,sid,txt,txt,ct,cid))
    conn.commit()

    angles = score.score_account(conn, ACC)
    print(f"{'rank':<5}{'score':<8}{'type':<11}{'srcs':<6}angle")
    for i,a in enumerate(angles,1):
        print(f"{i:<5}{a['total']:<8.3f}{a['claim_type']:<11}{len(a['sources']):<6}{a['canonical']}")
        print(f"     breakdown: {a['breakdown']}")
    print()
    assert angles[0]["claim_type"]=="objection", "cross-source objection should rank first"
    print("PASS: cross-source objection ranks #1")
    ident = [a for a in angles if a["claim_type"]=="identity"][0]
    benefit = [a for a in angles if a["claim_type"]=="benefit"][0]
    assert ident["breakdown"]["share"] > benefit["breakdown"]["share"], "share weighting"
    print("PASS: share-heavy identity angle scores higher on share than save-heavy benefit")

    # This used to print PASS without asserting anything, which meant a
    # broken boosted filter and a working one produced identical output.
    # The boosted post's save rate is 0.90, the highest in the account —
    # so if it were being counted, this cluster's save weight would
    # normalise to 1.0 rather than 0.0.
    boosted = [a for a in angles if a["canonical"] == "Boosted post, paid reach"][0]
    assert boosted["breakdown"]["save"] == 0.0, (
        "boosted engagement leaked into scoring: expected save weight 0.0, "
        f"got {boosted['breakdown']['save']}"
    )
    print("PASS: boosted post excluded from engagement rates")
