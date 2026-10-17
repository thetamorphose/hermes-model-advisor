from probe_common import *
import urllib.parse, time
B="https://datasets-server.huggingface.co"
def q(cfg,where,off=0):
    return f"{B}/filter?dataset=lmarena-ai/leaderboard-dataset&config={cfg}&split=latest&where={urllib.parse.quote(where)}&offset={off}&length=100"
s,b,h=get(q("text","\"category\"='overall'"))
print(s,{k:v for k,v in h.items() if "rate" in k.lower() or "retry" in k.lower()})
if s==200:
    d=json.loads(b); print(d["num_rows_total"], len(d["rows"]))
else: print(b[:200])
