import json,re,collections
n=json.load(open("nous_models.raw.json",encoding="utf-8"))["data"]
aa=json.load(open("aa_models.raw.json",encoding="utf-8"))["data"]
ar=json.load(open("arena.raw.json",encoding="utf-8"))
def k(s):
    s=s.lower(); s=re.sub(r"\(.*?\)","",s); s=s.replace(".","-").replace("_","-"); return re.sub(r"[^a-z0-9]+","-",s).strip("-")
EFF=r"-(max|xhigh|high|medium|low|minimal|non-reasoning|reasoning|thinking|default)$"
def norm(s):
    s=k(s)
    for _ in range(3): s=re.sub(EFF,"",s)
    return s
fams=collections.defaultdict(list)
for m in n:
    i=m["id"].lstrip("~"); i=re.sub(r":[A-Za-z0-9_-]+$","",i); i=re.sub(r"-latest$","",i)
    fams[i].append(m)
def has_aa(metric):
    s=set()
    for m in aa:
        if m["evaluations"].get(metric) is not None: s.add(norm(m["slug"]))
    return s
def has_ar(cfg,cat):
    return {norm(x["model_name"]) for x in ar[cfg] if x["category"]==cat}
src={"AA Intelligence":has_aa("artificial_analysis_intelligence_index"),"AA Coding":has_aa("artificial_analysis_coding_index"),"AA Math":has_aa("artificial_analysis_math_index"),
"AA tau2 (agentic)":has_aa("tau2"),"AA terminalbench_hard (agentic)":has_aa("terminalbench_hard"),"AA LCR (длинный контекст)":has_aa("lcr"),"AA IFBench":has_aa("ifbench"),
"Arena text overall":has_ar("text","overall"),"Arena text coding":has_ar("text","coding"),"Arena text math":has_ar("text","math"),"Arena text creative_writing":has_ar("text","creative_writing"),
"Arena text russian":has_ar("text","russian"),"Arena text longer_query":has_ar("text","longer_query"),"Arena text instruction_following":has_ar("text","instruction_following"),
"Arena vision overall":has_ar("vision","overall"),"Arena agent overall":has_ar("agent","overall"),"Arena webdev":has_ar("webdev","webdev")}
img=[f for f,ms in fams.items() if any("image" in (m["architecture"].get("input_modalities") or []) for m in ms)]
print("семейств",len(fams),"с image-входом",len(img))
for name,s in src.items():
    c=sum(1 for f in fams if norm(f.split("/",1)[1]) in s)
    ci=sum(1 for f in img if norm(f.split("/",1)[1]) in s)
    print(f"{name:38s} семейств Nous: {c:3d} ({100*c/len(fams):.0f}%), из image-семейств: {ci}")
# покрытие "общий рейтинг" в разных режимах
A=src["AA Intelligence"];R=src["Arena text overall"]
key=lambda f:norm(f.split("/",1)[1])
a=sum(1 for f in fams if key(f) in A); r=sum(1 for f in fams if key(f) in R)
u=sum(1 for f in fams if key(f) in A or key(f) in R)
print("общий рейтинг: только AA",a,"только Arena (резерв)",r,"AA или Arena",u,"из",len(fams))
# модели без chat-назначения
nonchat=[f for f,ms in fams.items() if any(w in f for w in ("embed","voyage","image","video","tts","whisper"))]
print("похожи на не-чат (embed/voyage/image/video):",len(nonchat),nonchat[:10])
# AA: сколько записей на модель; дубль-конфигураций
print("AA записей",len(aa),"уникальных норм.имён",len({norm(m['slug']) for m in aa}))
# arena: дубли имён с разными усилиями
c=collections.Counter(norm(x["model_name"]) for x in ar["text"] if x["category"]=="overall"); print("Arena overall дублей норм.имён:",sum(1 for v in c.values() if v>1))
# возраст данных
print("Arena даты:",{cfg:sorted({x['leaderboard_publish_date'] for x in rows}) for cfg,rows in ar.items()})
