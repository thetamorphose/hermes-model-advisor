from probe_common import *
k=aa_key()
def red(x): return x.replace(k,"<KEY>") if k else x
for u in ["https://artificialanalysis.ai/api/v2/data/llms/models","https://artificialanalysis.ai/api/v2/data/llms/models?limit=1"]:
    s,b,h=get(u,{"x-api-key":k,"User-Agent":"p/0"})
    print(u,s,len(b),{kk:v for kk,v in h.items() if kk.lower().startswith(("x-rate","ratelimit","retry","content-type"))})
    if s==200:
        (ROOT/"specs/001-model-advisor/stage0/aa_models.raw.json").write_bytes(b); break
    print(red(b[:300].decode("utf-8","replace")))
