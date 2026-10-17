from probe_common import *
import time, os
B="https://datasets-server.huggingface.co"
def fetch(url, tries=6):
    for i in range(tries):
        s,b,h=get(url)
        if s==200: return json.loads(b)
        wait=min(60,5*2**i)
        if s==429 and h.get("Retry-After","").isdigit(): wait=int(h["Retry-After"])+1
        print("  retry",s,"wait",wait,flush=True); time.sleep(wait)
    return None
cache=json.load(open("arena.raw.json",encoding="utf-8")) if os.path.exists("arena.raw.json") else {}
for cfg in ["text","vision","agent","webdev","document"]:
    if cfg in cache and cache[cfg]: print(cfg,"cached",len(cache[cfg])); continue
    rows=[];off=0;total=None
    while total is None or off<total:
        d=fetch(f"{B}/rows?dataset=lmarena-ai/leaderboard-dataset&config={cfg}&split=latest&offset={off}&length=100")
        if d is None: print(cfg,"FAILED at",off); break
        total=d["num_rows_total"]; r=[x["row"] for x in d["rows"]]
        if not r: break
        rows+=r; off+=len(r); time.sleep(0.7)
    if total is not None and len(rows)>=total:
        cache[cfg]=rows; json.dump(cache,open("arena.raw.json","w",encoding="utf-8"),ensure_ascii=False)
    print(cfg,len(rows),"total",total,flush=True)
