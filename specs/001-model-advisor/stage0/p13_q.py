import json,re,collections
n=json.load(open("nous_models.raw.json",encoding="utf-8"))["data"]
by={m["id"]:m for m in n}
slug2id=collections.defaultdict(list)
for m in n: slug2id[m["canonical_slug"]].append(m["id"])
alias=[m for m in n if m["id"].startswith("~") or m["id"].endswith("-latest")]
print("алиасов",len(alias))
res=collections.Counter()
for m in alias:
    cs=m["canonical_slug"]; tgt=[i for i in slug2id[cs] if i!=m["id"]]
    # цель по canonical_slug совпадает с id другой записи?
    direct=cs in by and cs!=m["id"]
    same_price=[i for i in tgt if by[i]["pricing"]["prompt"]==m["pricing"]["prompt"] and by[i]["pricing"]["completion"]==m["pricing"]["completion"]]
    res["canonical_slug указывает на другую запись" if direct else ("другая запись с тем же slug" if tgt else "цель не найдена")]+=1
    print(f"{m['id']:42s} slug={cs:44s} direct={direct} others={tgt[:2]}")
print(res)
# сколько записей вообще имеют slug != id
print("записей, где canonical_slug != id:",sum(1 for m in n if m["canonical_slug"]!=m["id"].lstrip("~")), "из",len(n))
print([ (m["id"],m["canonical_slug"]) for m in n if m["id"].startswith("anthropic/claude-opus")][:6])
# усилия: модели с несколькими конфигурациями без medium
def eff(s):
    m=re.search(r"\((.*?)\)",s)
    if m:
        for e in ("xhigh","max","high","medium","low","minimal","non-reasoning"):
            if e in m.group(1).lower().replace(" ","-"): return e
    return None
aa=json.load(open("aa_models.raw.json",encoding="utf-8"))["data"]
def k(s):
    s=s.lower(); s=re.sub(r"\(.*?\)","",s); s=s.replace(".","-").replace("_","-"); return re.sub(r"[^a-z0-9]+","-",s).strip("-")
g=collections.defaultdict(dict)
for m in aa:
    v=m["evaluations"].get("artificial_analysis_intelligence_index")
    if v is None: continue
    g[k(m["name"].split("(")[0])][eff(m["name"]) or "-"]=v
multi_nomed=[(a,v) for a,v in g.items() if len(v)>1 and "medium" not in v]
print("AA моделей: всего",len(g),"с medium",sum(1 for v in g.values() if "medium" in v),"несколько конфигураций без medium",len(multi_nomed),"одна конфигурация",sum(1 for v in g.values() if len(v)==1))
for a,v in multi_nomed[:8]: print("  ",a,v)
