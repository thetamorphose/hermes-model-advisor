import json,re,collections,statistics
n=json.load(open("nous_models.raw.json",encoding="utf-8"))["data"]
aa=json.load(open("aa_models.raw.json",encoding="utf-8"))["data"]
ar=json.load(open("arena.raw.json",encoding="utf-8"))

# --- семейства Nous
def base_id(i):
    i=i.lstrip("~"); i=re.sub(r":[A-Za-z0-9_-]+$","",i)
    i=re.sub(r"-latest$","",i)
    i=re.sub(r"-(20\d{6}|\d{4}-\d{2}-\d{2}|\d{6})$","",i)
    return i
fam=collections.defaultdict(list)
for m in n: fam[base_id(m["id"])].append(m)
print("записей",len(n),"семейств",len(fam))
suffix=collections.Counter()
for m in n:
    mm=re.search(r":([A-Za-z0-9_-]+)$",m["id"]); 
    if mm: suffix[mm.group(1)]+=1
    if m["id"].startswith("~"): suffix["~"]+=1
    if m["id"].endswith("-latest"): suffix["-latest"]+=1
print("суффиксы",suffix)
# расхождение цены внутри семейства
diff=0
for f,ms in fam.items():
    ps={(m["pricing"].get("prompt"),m["pricing"].get("completion")) for m in ms}
    if len(ps)>1: diff+=1
print("семейств с разной ценой вариантов",diff)
nomodel=[f for f,ms in fam.items() if not any(m["id"]==f for m in ms)]
print("семейств без базового варианта (нет записи без суффикса)",len(nomodel),nomodel[:8])

# --- ключи
def k(s): 
    s=s.lower(); s=re.sub(r"\(.*?\)","",s); s=s.replace(".","-").replace("_","-"); s=re.sub(r"[^a-z0-9]+","-",s); return s.strip("-")
# AA: slug -> варианты. базовый = slug без суффикса усилия
EFF=r"-(max|xhigh|high|medium|low|minimal|non-reasoning|reasoning|thinking|default)$"
aa_by=collections.defaultdict(list)
for m in aa: aa_by[k(m["slug"])].append(m)
aa_base=collections.defaultdict(list)
for m in aa: aa_base[re.sub(EFF,"",k(m["slug"]))].append(m)
print("AA слагов",len(aa_by),"баз без усилия",len(aa_base))
# Arena text overall
ov=[x for x in ar["text"] if x["category"]=="overall"]
ar_by=collections.defaultdict(list)
for x in ov: ar_by[k(x["model_name"])].append(x)
ar_base=collections.defaultdict(list)
for x in ov: ar_base[re.sub(EFF,"",k(x["model_name"]))].append(x)
print("Arena overall",len(ov),"баз",len(ar_base))

res={}
for f,ms in fam.items():
    key=k(f.split("/",1)[1])
    a=aa_by.get(key) or []
    ab=aa_base.get(key) or []
    r=ar_by.get(key) or []
    rb=ar_base.get(key) or []
    res[f]=dict(aa_exact=len(a),aa_base=len(ab),ar_exact=len(r),ar_base=len(rb))
def pct(x): return f"{x} ({100*x/len(fam):.0f}%)"
print("AA точное совпадение слага:",pct(sum(1 for v in res.values() if v["aa_exact"]>=1)))
print("AA по базе (любой вариант усилия):",pct(sum(1 for v in res.values() if v["aa_base"]>=1)))
print("AA несколько кандидатов по базе:",sum(1 for v in res.values() if v["aa_base"]>1))
print("Arena точное:",pct(sum(1 for v in res.values() if v["ar_exact"]>=1)))
print("Arena по базе:",pct(sum(1 for v in res.values() if v["ar_base"]>=1)))
print("Arena несколько кандидатов по базе:",sum(1 for v in res.values() if v["ar_base"]>1))
both=[f for f,v in res.items() if v["aa_base"]>=1 and v["ar_base"]>=1]
onlyar=[f for f,v in res.items() if v["aa_base"]==0 and v["ar_base"]>=1]
onlyaa=[f for f,v in res.items() if v["aa_base"]>=1 and v["ar_base"]==0]
none=[f for f,v in res.items() if v["aa_base"]==0 and v["ar_base"]==0]
print("оба:",len(both),"только Arena:",len(onlyar),"только AA:",len(onlyaa),"ни одного:",len(none))
print("только Arena примеры:",onlyar[:15]); print("ни одного примеры:",none[:25])
json.dump(dict(both=both,onlyar=onlyar,onlyaa=onlyaa,none=none),open("match_stats.json","w"),ensure_ascii=False)
# примеры вариантов
for key in ["claude-opus-5-5","gpt-6-sol"]:
    print(key,[x["name"] for x in aa_base.get(key,[])],[x["model_name"] for x in ar_base.get(key,[])])
