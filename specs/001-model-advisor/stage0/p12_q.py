import json,re,collections
n=json.load(open("nous_models.raw.json",encoding="utf-8"))["data"]
aa=json.load(open("aa_models.raw.json",encoding="utf-8"))["data"]
ar=json.load(open("arena.raw.json",encoding="utf-8"))
# --- Q1: усилие
def eff(s):
    m=re.search(r"\((.*?)\)",s)
    if m:
        for e in ("xhigh","max","high","medium","low","minimal","non-reasoning"):
            if e in m.group(1).lower().replace(" ","-"): return e
    m=re.search(r"-(xhigh|max|high|medium|low|minimal|non-reasoning)$",s.lower())
    return m.group(1) if m else "нет-метки"
print("AA усилия:",collections.Counter(eff(m["name"]) for m in aa).most_common())
ov=[x for x in ar["text"] if x["category"]=="overall"]
print("Arena усилия:",collections.Counter(eff(x["model_name"]) for x in ov).most_common())
def k(s):
    s=s.lower(); s=re.sub(r"\(.*?\)","",s); s=s.replace(".","-").replace("_","-"); s=re.sub(r"[^a-z0-9]+","-",s).strip("-")
    for _ in range(3): s=re.sub(r"-(max|xhigh|high|medium|low|minimal|non-reasoning|reasoning|thinking|default)$","",s)
    return s
g=collections.defaultdict(set)
for m in aa:
    if m["evaluations"].get("artificial_analysis_intelligence_index") is not None: g[k(m["slug"])].add(eff(m["name"]))
tot=len(g); med=sum(1 for v in g.values() if "medium" in v)
print("AA моделей с Intelligence:",tot,"из них есть medium:",med)
only=collections.Counter(next(iter(v)) for v in g.values() if len(v)==1); print("AA моделей с единственной конфигурацией:",only)
ga=collections.defaultdict(set)
for x in ov: ga[k(x["model_name"])].add(eff(x["model_name"]))
print("Arena моделей:",len(ga),"есть medium:",sum(1 for v in ga.values() if "medium" in v),"одна конфигурация:",sum(1 for v in ga.values() if len(v)==1))
# насколько разные оценки по усилию (AA)
d=collections.defaultdict(dict)
for m in aa:
    v=m["evaluations"].get("artificial_analysis_intelligence_index")
    if v is not None: d[k(m["slug"])][eff(m["name"])]=v
ex=[(a,v) for a,v in d.items() if "medium" in v and "max" in v][:4]
for a,v in ex: print(a,v)
# --- Q2: семейства без базового варианта
def base(i):
    i=i.lstrip("~"); i=re.sub(r":[A-Za-z0-9_-]+$","",i); return re.sub(r"-latest$","",i)
fam=collections.defaultdict(list)
for m in n: fam[base(m["id"])].append(m)
rows=[]
for f,ms in fam.items():
    if any(m["id"]==f for m in ms): continue
    rows.append((f,[(m["id"],m["created"],m["pricing"]["prompt"],m["pricing"]["completion"]) for m in ms]))
print(len(rows))
for f,v in rows: print(f); [print("    ",x) for x in v]
