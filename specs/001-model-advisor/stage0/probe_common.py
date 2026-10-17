"""Одноразовые пробы этапа 0. Секреты не печатаются."""
import json, os, re, urllib.request, urllib.error, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[3]

def aa_key():
    k = os.environ.get("AA_API_KEY")
    if k: return k
    for l in (ROOT/".env").read_text(encoding="utf-8").splitlines():
        if l.startswith("AA_API_KEY="): return l.split("=",1)[1].strip()

def nous_token():
    t = os.environ.get("NOUS_API_TOKEN")
    if t: return t
    p = pathlib.Path(os.environ["LOCALAPPDATA"])/"hermes"/"auth.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["providers"]["nous"]["agent_key"]

def get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {"User-Agent":"model-advisor-probe/0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return None, repr(e).encode(), {}
