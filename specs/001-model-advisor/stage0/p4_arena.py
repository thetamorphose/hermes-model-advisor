from probe_common import *
B="https://datasets-server.huggingface.co"
s,b,_=get(B+"/splits?dataset=lmarena-ai/leaderboard-dataset")
sp=json.loads(b)["splits"]
cfgs=sorted({x["config"] for x in sp if x["split"]=="latest"})
print(len(cfgs)); print(cfgs)
s,b,_=get(B+"/first-rows?dataset=lmarena-ai/leaderboard-dataset&config=text&split=latest")
d=json.loads(b); print(s,[f["name"] for f in d["features"]]); 
for r in d["rows"][:3]: print(json.dumps(r["row"],ensure_ascii=False)[:600])
