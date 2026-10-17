from probe_common import *
import time
B="https://datasets-server.huggingface.co"
out={}
for cfg in ["text","vision","agent","webdev","document"]:
    rows=[];off=0;calls=0
    while True:
        s,b,_=get(f"{B}/rows?dataset=lmarena-ai/leaderboard-dataset&config={cfg}&split=latest&offset={off}&length=100")
        calls+=1
        if s!=200: print(cfg,"ERR",s,b[:150]); break
        d=json.loads(b); r=[x["row"] for x in d["rows"]]
        rows+=r; off+=len(r)
        if not r or off>=d["num_rows_total"]: break
    out[cfg]=rows
    from collections import Counter
    print(cfg,len(rows),"calls",calls,Counter(x["category"] for x in rows).most_common(40))
json.dump(out,open("arena.raw.json","w",encoding="utf-8"),ensure_ascii=False)
