from probe_common import *
u="https://artificialanalysis.ai/api/v2/data/llms/models"
for label,h in [("без ключа",{"User-Agent":"p/0"}),("мусорный ключ",{"x-api-key":"invalid-key-000","User-Agent":"p/0"})]:
    s,b,hd=get(u,h); print(label,s,b[:160].decode("utf-8","replace").replace("\n"," "))
s,b,hd=get(u,{"x-api-key":aa_key(),"User-Agent":"p/0"})
print("с ключом",s,{k:v for k,v in hd.items() if any(w in k.lower() for w in ("limit","remaining","reset","retry"))})
