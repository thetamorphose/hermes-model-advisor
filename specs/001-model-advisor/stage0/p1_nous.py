from probe_common import *
u="https://inference-api.nousresearch.com/v1/models"
for label,h in [("noauth",{"User-Agent":"p/0"}),("auth",{"User-Agent":"p/0","Authorization":"Bearer "+nous_token()})]:
    s,b,_=get(u,h)
    print(label,s,len(b))
    if s==200:
        d=json.loads(b); (ROOT/"specs/001-model-advisor/stage0/nous_models.raw.json").write_bytes(b)
        print(list(d.keys()), len(d["data"])); print(json.dumps(d["data"][0],ensure_ascii=False)[:800]); break
    else: print(b[:200])
