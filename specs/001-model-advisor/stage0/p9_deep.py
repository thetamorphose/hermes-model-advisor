import json,re,collections,statistics,difflib
n=json.load(open("nous_models.raw.json",encoding="utf-8"))["data"]
aa=json.load(open("aa_models.raw.json",encoding="utf-8"))["data"]
ar=json.load(open("arena.raw.json",encoding="utf-8"))
st=json.load(open("match_stats.json"))
def k(s):
    s=s.lower(); s=re.sub(r"\(.*?\)","",s); s=s.replace(".","-").replace("_","-"); return re.sub(r"[^a-z0-9]+","-",s).strip("-")
EFF=r"-(max|xhigh|high|medium|low|minimal|non-reasoning|reasoning|thinking|default|preview|instruct|it|chat)$"
def norm(s):
    s=k(s)
    for _ in range(3): s=re.sub(EFF,"",s)
    s=re.sub(r"-(20\d{6}|\d{4}|\d{6})$","",s)
    return s
aa_names={norm(m["slug"]) for m in aa}|{norm(m["name"]) for m in aa}
ov=[x for x in ar["text"] if x["category"]=="overall"]
ar_names={norm(x["model_name"]) for x in ov}
allk=aa_names|ar_names
miss=st["none"]
rec=0;near=[];real=[]
for f in miss:
    key=norm(f.split("/",1)[1])
    if key in allk: rec+=1; continue
    c=difflib.get_close_matches(key,allk,n=1,cutoff=0.8)
    (near if c else real).append((f,c[0] if c else None))
print("none:",len(miss),"восстановлено агрессивной нормализацией:",rec,"нечёткие кандидаты (нужна проверка):",len(near),"нет аналога:",len(real))
print("нечёткие:",near[:12]); print("нет аналога:",[f for f,_ in real][:30])
# возраст нематченных
byid={m["id"]:m for m in n}
import time
def created(f): 
    m=byid.get(f) or next((x for x in n if x["id"].lstrip("~").startswith(f)),None); return m["created"] if m else 0
# согласованность шкал
def base_ids(s): return norm(s)
aa_ov={}
for m in aa:
    v=m["evaluations"].get("artificial_analysis_intelligence_index")
    if v is not None: aa_ov.setdefault(norm(m["slug"]),[]).append(v)
ar_ov=collections.defaultdict(list)
for x in ov: ar_ov[norm(x["model_name"])].append(x["rating"])
pairs=[(max(aa_ov[a]),max(ar_ov[a])) for a in aa_ov if a in ar_ov]
print("пар AA×Arena по нормализованным именам:",len(pairs))
def ranks(v):
    o=sorted(range(len(v)),key=lambda i:v[i]); r=[0]*len(v)
    for i,j in enumerate(o): r[j]=i
    return r
x=[p[0] for p in pairs]; y=[p[1] for p in pairs]
rx,ry=ranks(x),ranks(y)
print("Spearman:",round(statistics.correlation(rx,ry),3),"Pearson:",round(statistics.correlation(x,y),3))
print("AA диапазон",min(x),max(x),"Arena диапазон",round(min(y)),round(max(y)))
# насколько часто выбор флагмана отличается при смене источника
nf=collections.defaultdict(list)
for m in n:
    dev=m["id"].split("/")[0].lstrip("~"); key=norm(m["id"].split("/",1)[1].split(":")[0])
    nf[dev].append(key)
diffdev=0;bothdev=0;same=0
for dev,keys in nf.items():
    a={kk:max(aa_ov[kk]) for kk in set(keys) if kk in aa_ov}; r={kk:max(ar_ov[kk]) for kk in set(keys) if kk in ar_ov}
    if a and r:
        bothdev+=1
        if max(a,key=a.get)!=max(r,key=r.get): diffdev+=1
print("разработчиков с обоими рейтингами:",bothdev,"из них флагман различается:",diffdev)
devs=set(); dev_aa=set(); dev_ar=set()
for dev,keys in nf.items():
    devs.add(dev)
    if any(kk in aa_ov for kk in keys): dev_aa.add(dev)
    if any(kk in ar_ov for kk in keys): dev_ar.add(dev)
print("разработчиков",len(devs),"с AA",len(dev_aa),"с Arena",len(dev_ar),"только Arena",len(dev_ar-dev_aa),"ни одного",len(devs-dev_aa-dev_ar))
