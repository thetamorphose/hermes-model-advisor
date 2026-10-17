from probe_common import *
B="https://datasets-server.huggingface.co"
for path in ["/splits?dataset=lmarena-ai/leaderboard-dataset","/is-valid?dataset=lmarena-ai/leaderboard-dataset"]:
    s,b,_=get(B+path); print(path,s); print(b[:3000].decode("utf-8","replace"))
