"""Тесты fetch_scores.py (AC-3, AC-4, AC-18, правила шага 3). Сеть не нужна: HTTP-слой подменяется."""
import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import fetch_scores as fs  # noqa: E402

CFG = fs.load_config(ROOT / "skill" / "config.json")
SECRET = "SECRET-KEY-0123456789-abcdefghijkl"
NOW = "2026-10-17T12:00:00Z"


def fam(fid, alias=False, chat=True):
    return {"family_id": fid, "representative_id": fid, "alias": alias, "chat": chat}


FAMILIES = [
    fam("vendor/alpha"), fam("vendor/beta"), fam("vendor/gamma"), fam("vendor/delta-pro"),
    fam("vendor/delta"), fam("vendor/only-arena"), fam("vendor/embed", chat=False),
    fam("~vendor/alpha-latest", alias=True), fam("vendor/alpha-latest-x"), fam("vendor/gptlike-sol"),
    fam("vendor/gptlike-sol-max-2"),
]


def aa_rec(name, intel=None, coding=None, tb=None, date="2026-09-01"):
    return {"name": name, "slug": "x", "release_date": date,
            "evaluations": {"artificial_analysis_intelligence_index": intel,
                            "artificial_analysis_coding_index": coding, "terminalbench_hard": tb}}


AA_PAYLOAD = {"status": 200, "data": [
    aa_rec("Alpha (Max)", 60, 50, 0.5), aa_rec("Alpha (High)", 55, 45, 0.4), aa_rec("Alpha (Medium)", 50, None, None),
    aa_rec("Beta (Low)", 30), aa_rec("Beta (High)", 40), aa_rec("Beta (Max)", 45),   # нет Medium: high
    aa_rec("Gamma (Reasoning)", 20), aa_rec("Gamma (Non-reasoning)", 10),
    aa_rec("Delta", 33),
    aa_rec("Unmapped Model", 70),
]}


def arena_rows(cat, items, date="2026-10-15", field="rating"):
    return [{"model_name": n, "category": cat, field: v, "leaderboard_publish_date": d or date} for n, v, d in items]


ARENA_RAW = {
    "text": arena_rows("overall", [("alpha-max", 1500, None), ("beta", 1400, None), ("only-arena", 1300, None),
                                   ("Gamma (High)", 1200, None), ("delta-pro", 1250, None)])
    + arena_rows("coding", [("alpha-max", 1550, None), ("only-arena", 1280, None), ("beta", 1390, None)])
    + arena_rows("math", [("alpha-max", 9999, None)]),
    "agent": arena_rows("overall", [("Alpha (Max)", 0.2, None), ("only-arena", 0.05, None)], field="score"),
    "vision": arena_rows("overall", [("only-arena", 1100, "2026-01-09"), ("only-arena", 1150, "2026-10-09"),
                                     ("alpha-max", 1200, None)]),
}


def collect(aa=AA_PAYLOAD, arena=ARENA_RAW, families=FAMILIES, key=None):
    return fs.collect(families, CFG, key, NOW, aa_payload=aa, arena_raw=arena)


class NormalizationTests(unittest.TestCase):
    def test_parse_name_strips_parens_and_effort(self):
        self.assertEqual(fs.parse_name("Claude Opus 5.5 (Max, Default Fallback)"), ("claude-opus-5-5", "max"))
        self.assertEqual(fs.parse_name("Claude Opus 4.6 (Non-reasoning, High)"), ("claude-opus-4-6", "non-reasoning"))
        self.assertEqual(fs.parse_name("Muse Spark 1.2 (xHigh)"), ("muse-spark-1-2", "xhigh"))
        self.assertEqual(fs.parse_name("kimi-k2.5-instant"), ("kimi-k2-5-instant", None))

    def test_non_effort_parens_dropped_without_effort(self):
        self.assertEqual(fs.parse_name("GPT-4o (Nov '24)"), ("gpt-4o", None))

    def test_suffix_effort_only_when_base_in_catalog(self):
        keys = {"gpt-6-sol"}
        self.assertEqual(fs.resolve_key("gpt-6-sol-max", None, keys), ("gpt-6-sol", "max"))
        # полное имя есть в каталоге -> суффикс часть имени
        self.assertEqual(fs.resolve_key("gpt-6-sol-max", None, {"gpt-6-sol", "gpt-6-sol-max"}), ("gpt-6-sol-max", None))
        # базы нет -> ничего не режем
        self.assertEqual(fs.resolve_key("foo-high", None, keys), ("foo-high", None))


class ConfigPickTests(unittest.TestCase):
    def test_medium_preferred_then_order(self):
        s, _ = collect()
        self.assertEqual(s["models"]["vendor/alpha"]["overall"]["value"], 50)       # Medium
        self.assertEqual(s["models"]["vendor/alpha"]["overall"]["config"], "medium")
        self.assertEqual(s["models"]["vendor/beta"]["overall"]["value"], 40)        # нет Medium -> High
        self.assertEqual(s["models"]["vendor/beta"]["overall"]["config"], "high")

    def test_single_config_taken_as_is_and_label_saved(self):
        s, _ = collect()
        self.assertEqual(s["models"]["vendor/delta"]["overall"]["value"], 33)
        self.assertIsNone(s["models"]["vendor/delta"]["overall"]["config"])

    def test_config_chosen_per_metric(self):
        # у Medium нет Coding Index: coding берётся из High (первая в порядке с значением)
        s, _ = collect()
        self.assertEqual(s["models"]["vendor/alpha"]["coding"]["value"], 45)
        self.assertEqual(s["models"]["vendor/alpha"]["coding"]["config"], "high")

    def test_reasoning_before_non_reasoning(self):
        s, _ = collect()
        self.assertEqual(s["models"]["vendor/gamma"]["overall"]["value"], 20)

    def test_deterministic_tie_break(self):
        a, _ = collect()
        b, _ = collect(aa={"data": list(reversed(AA_PAYLOAD["data"]))})
        self.assertEqual(a["models"], b["models"])


class MatchingTests(unittest.TestCase):
    def test_no_fuzzy_scores_and_candidate_recorded(self):
        s, nm = collect()
        self.assertNotIn("vendor/delta-pro", [k for k, v in s["models"].items() if v["overall"] and v["overall"]["source"] == "artificial_analysis"
                                                and v["overall"]["matched_name"] == "Delta"])
        # delta-pro имеет только точное совпадение в Arena ('delta-pro'), оценку Delta (AA) получить не может
        self.assertEqual(s["models"]["vendor/delta-pro"]["overall"]["source"], "lmarena")

    def test_fuzzy_goes_to_candidates_only(self):
        fams = [fam("vendor/nova-lite-v1")]
        aa = {"data": [aa_rec("Nova Lite", 10)]}
        s, nm = collect(aa=aa, arena={"text": arena_rows("overall", [("zzz", 1, None)])}, families=fams)
        self.assertNotIn("vendor/nova-lite-v1", s["models"])
        self.assertEqual([c["status"] for c in nm["candidates"]], ["candidate"])
        self.assertEqual(nm["candidates"][0]["family_id"], "vendor/nova-lite-v1")

    def test_alias_and_nonexistent_get_nothing(self):
        aa = {"data": [aa_rec("Alpha Latest", 77), aa_rec("Embed", 5)]}
        s, _ = collect(aa=aa, arena={"text": arena_rows("overall", [("x", 1, None)])},
                       families=[fam("~vendor/alpha-latest", alias=True), fam("vendor/alpha-latest")])
        self.assertNotIn("~vendor/alpha-latest", s["models"])

    def test_nonchat_may_have_scores(self):
        s, _ = collect(aa={"data": [aa_rec("Embed", 5)]}, arena={"text": arena_rows("overall", [("x", 1, None)])})
        self.assertIn("vendor/embed", s["models"])

    def test_suffix_effort_in_arena_matches_base(self):
        s, _ = collect()
        self.assertEqual(s["models"]["vendor/alpha"]["coding"]["config"], "high")  # AA Alpha(High) выше Arena alpha-max
        # only-arena: нет в AA, берём Arena; без усилия в имени
        self.assertEqual(s["models"]["vendor/only-arena"]["overall"]["source"], "lmarena")

    def test_ambiguous_catalog_key_gets_no_scores(self):
        fams = [fam("a/same"), fam("b/same")]
        s, nm = collect(aa={"data": [aa_rec("Same", 5)]}, arena={"text": arena_rows("overall", [("x", 1, None)])},
                        families=fams)
        self.assertEqual(s["models"], {})
        self.assertIn("same", nm["ambiguous_catalog_keys"])


class SourceSelectionTests(unittest.TestCase):
    def test_aa_priority_arena_when_missing(self):
        s, _ = collect()
        alpha = s["models"]["vendor/alpha"]
        self.assertEqual(alpha["overall"]["source"], "artificial_analysis")
        self.assertEqual(alpha["agentic"]["source"], "artificial_analysis")
        beta = s["models"]["vendor/beta"]
        self.assertEqual(beta["coding"]["source"], "lmarena")        # AC-3: аналог из LMArena с пометкой
        self.assertEqual(beta["coding"]["metric"], "Arena text coding")

    def test_value_has_source_metric_date(self):
        s, _ = collect()
        for rec in s["models"].values():
            for v in rec.values():
                if v:
                    self.assertTrue({"value", "source", "metric", "date"} <= set(v))

    def test_missing_is_null_not_zero(self):
        s, _ = collect()
        self.assertIsNone(s["models"]["vendor/delta"]["vision"])
        self.assertIsNone(s["models"]["vendor/delta"]["coding"])

    def test_other_arena_categories_ignored(self):
        s, _ = collect()
        self.assertNotEqual(s["models"]["vendor/alpha"]["overall"]["value"], 9999)

    def test_vision_newest_date_wins(self):
        s, _ = collect()
        v = s["models"]["vendor/only-arena"]["vision"]
        self.assertEqual((v["value"], v["date"]), (1150.0, "2026-10-09"))

    def test_status_ok(self):
        s, _ = collect()
        self.assertEqual(s["sources"]["artificial_analysis"]["status"], "ok")
        self.assertEqual(s["sources"]["lmarena"]["status"], "ok")
        self.assertEqual(s["sources"]["lmarena"]["license"], "CC-BY-4.0")
        self.assertEqual(s["mode"], "full")
        self.assertEqual(s["schema_version"], fs.SCHEMA_VERSION)


def fake_http(code, body=b"{}", exc=None):
    def _get(url, headers=None, timeout=60):
        if exc:
            raise exc
        return code, body
    return _get


class FallbackTests(unittest.TestCase):
    """AC-4: любой отказ AA -> успех на LMArena, status=unavailable и код причины."""

    def run_aa(self, key, **kw):
        with mock.patch.object(fs, "http_get", fake_http(**kw)):
            return fs.fetch_aa(CFG, key, "2026-10-17")

    def test_reason_codes(self):
        self.assertEqual(self.run_aa(None, code=200)[0], {"status": "unavailable", "reason": "no_key"})
        self.assertEqual(self.run_aa(SECRET, code=401)[0]["reason"], "auth_error")
        self.assertEqual(self.run_aa(SECRET, code=403)[0]["reason"], "auth_error")
        self.assertEqual(self.run_aa(SECRET, code=429)[0]["reason"], "rate_limited")
        self.assertEqual(self.run_aa(SECRET, code=500)[0]["reason"], "bad_response")
        self.assertEqual(self.run_aa(SECRET, code=200, body=b"<html>not json")[0]["reason"], "bad_response")
        self.assertEqual(self.run_aa(SECRET, code=200, body=b'{"data": []}')[0]["reason"], "bad_response")
        self.assertEqual(self.run_aa(SECRET, code=200, body=b'{"foo": 1}')[0]["reason"], "bad_response")
        self.assertEqual(self.run_aa(SECRET, code=0, exc=fs.NetworkError("URLError"))[0]["reason"], "network")
        self.assertEqual(self.run_aa(SECRET, code=0, exc=RuntimeError("boom " + SECRET))[0]["reason"], "network")

    def test_success_parses(self):
        st, entries = self.run_aa(SECRET, code=200, body=json.dumps(AA_PAYLOAD).encode())
        self.assertEqual(st["status"], "ok")
        self.assertTrue(entries)

    def test_fallback_uses_arena_only_and_never_aa(self):
        for key, kw in [(None, {}), (SECRET, {"aa": None})]:
            with mock.patch.object(fs, "http_get", fake_http(401)):
                res = fs.collect(FAMILIES, CFG, key, NOW, aa_payload=None, arena_raw=ARENA_RAW)
            s, _ = res
            self.assertEqual(s["mode"], "fallback")
            self.assertEqual(s["sources"]["artificial_analysis"]["status"], "unavailable")
            for rec in s["models"].values():
                for v in rec.values():
                    if v:
                        self.assertEqual(v["source"], "lmarena")
            self.assertEqual(s["models"]["vendor/alpha"]["agentic"]["source"], "lmarena")

    def test_previous_snapshot_aa_values_not_carried(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "scores.json"
            out.write_text(json.dumps({"models": {"vendor/alpha": {"overall": {"value": 99, "source": "artificial_analysis"}}}}))
            s, _ = fs.collect(FAMILIES, CFG, None, NOW, aa_payload={"data": []}, arena_raw=ARENA_RAW)
            self.assertNotIn("99", json.dumps(s))
            self.assertNotEqual(s["models"]["vendor/alpha"]["overall"]["source"], "artificial_analysis")

    def test_arena_failure_does_not_break_aa(self):
        s, _ = fs.collect(FAMILIES, CFG, None, NOW, aa_payload=AA_PAYLOAD, arena_raw={})
        self.assertEqual(s["mode"], "aa_only")
        self.assertEqual(s["sources"]["lmarena"]["status"], "unavailable")
        self.assertIn("reason", s["sources"]["lmarena"])
        self.assertEqual(s["models"]["vendor/alpha"]["overall"]["source"], "artificial_analysis")
        self.assertIsNone(s["models"]["vendor/alpha"]["vision"])

    def test_both_down_returns_none(self):
        self.assertIsNone(fs.collect(FAMILIES, CFG, None, NOW, aa_payload={"data": []}, arena_raw={}))

    def test_exception_in_adapter_does_not_escape(self):
        with mock.patch.object(fs, "fetch_aa", side_effect=RuntimeError("x " + SECRET)):
            s, _ = fs.collect(FAMILIES, CFG, SECRET, NOW, aa_payload=None, arena_raw=ARENA_RAW)
        self.assertEqual(s["sources"]["artificial_analysis"], {"status": "unavailable", "reason": "bad_response"})


class ArenaFetchTests(unittest.TestCase):
    LC = dict(CFG["lmarena"], max_attempts=4, base_delay_s=1, max_delay_s=4)

    def page(self, rows, total, partial=False):
        return json.dumps({"rows": [{"row": r} for r in rows], "num_rows_total": total, "partial": partial}).encode()

    def test_retry_with_backoff_then_success(self):
        seq = [(429, b""), (500, b'{"error":"index loading"}'), (200, self.page([{"a": 1}], 1))]
        sleeps = []
        with mock.patch.object(fs, "http_get", lambda *a, **k: seq.pop(0)):
            data, reason = fs.arena_get_json("u", self.LC, sleeps.append)
        self.assertIsNone(reason)
        self.assertEqual(sleeps, [1, 2])

    def test_gives_up_with_reason(self):
        with mock.patch.object(fs, "http_get", lambda *a, **k: (429, b"")):
            data, reason = fs.arena_get_json("u", self.LC, lambda s: None)
        self.assertEqual((data, reason), (None, "rate_limited"))

    def test_pagination_complete(self):
        pages = {0: self.page([{"i": 0}, {"i": 1}], 3), 2: self.page([{"i": 2}], 3)}
        def get(url, headers=None, timeout=60):
            off = int(url.split("offset=")[1].split("&")[0])
            return 200, pages[off]
        with mock.patch.object(fs, "http_get", get):
            rows, ok, reason = fs.arena_pages(lambda off: f"x?offset={off}&y", self.LC, lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(len(rows), 3)

    def test_incomplete_load_reported_not_passed_as_full(self):
        def get(url, headers=None, timeout=60):
            return (200, self.page([{"i": 0}], 5)) if "offset=0" in url else (429, b"")
        with mock.patch.object(fs, "http_get", get):
            rows, ok, reason = fs.arena_pages(lambda off: f"x?offset={off}", self.LC, lambda s: None)
        self.assertFalse(ok)
        self.assertEqual(reason, "rate_limited")

    def test_partial_flag_means_incomplete(self):
        with mock.patch.object(fs, "http_get", lambda *a, **k: (200, self.page([{"i": 0}], 1, partial=True))):
            rows, ok, reason = fs.arena_pages(lambda off: "x", self.LC, lambda s: None)
        self.assertFalse(ok)

    def test_filter_failure_falls_back_to_rows(self):
        calls = []
        def get(url, headers=None, timeout=60):
            calls.append(url.split("?")[0].rsplit("/", 1)[1])
            if "/filter" in url:
                return 500, b'{"error":"the dataset index is loading"}'
            return 200, self.page([{"model_name": "m", "category": "overall", "rating": 1.0},
                                   {"model_name": "m", "category": "coding", "rating": 2.0}], 2)
        cfg = {"lmarena": dict(self.LC, metrics={"overall": CFG["lmarena"]["metrics"]["overall"],
                                                 "coding": CFG["lmarena"]["metrics"]["coding"]})}
        with mock.patch.object(fs, "http_get", get):
            got, incomplete = fs.fetch_arena_rows(cfg, sleep=lambda s: None)
        self.assertEqual(incomplete, {})
        self.assertEqual([r["rating"] for r in got["overall"]], [1.0])
        self.assertEqual([r["rating"] for r in got["coding"]], [2.0])
        self.assertIn("rows", calls)

    def test_partial_status_recorded(self):
        st = fs.arena_status(CFG, {"overall": [{"leaderboard_publish_date": "2026-10-15"}]}, {"coding": "rate_limited"})
        self.assertEqual(st["status"], "partial")
        self.assertEqual(st["incomplete"], {"coding": "rate_limited"})
        self.assertEqual(st["leaderboard_date"], "2026-10-15")

    def test_arena_unavailable_when_nothing(self):
        st = fs.arena_status(CFG, {}, {"overall": "network"})
        self.assertEqual(st, {"status": "unavailable", "reason": "network"})


class KeyTests(unittest.TestCase):
    def test_env_then_skill_env_then_root_env(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            (Path(b) / ".env").write_text("OTHER=1\nAA_API_KEY=\"root-key\"\n", encoding="utf-8")
            self.assertEqual(fs.find_aa_key({}, [a, b]), "root-key")
            (Path(a) / ".env").write_text("AA_API_KEY=skill-key\n", encoding="utf-8")
            self.assertEqual(fs.find_aa_key({}, [a, b]), "skill-key")
            self.assertEqual(fs.find_aa_key({"AA_API_KEY": "env-key"}, [a, b]), "env-key")
            self.assertIsNone(fs.find_aa_key({"AA_API_KEY": "  "}, [tempfile.gettempdir() + "/none-xyz"]))

    def test_key_sent_only_in_header(self):
        seen = {}
        def get(url, headers=None, timeout=60):
            seen.update(url=url, headers=headers)
            return 200, json.dumps(AA_PAYLOAD).encode()
        with mock.patch.object(fs, "http_get", get):
            fs.fetch_aa(CFG, SECRET, "2026-10-17")
        self.assertNotIn(SECRET, seen["url"])
        self.assertEqual(seen["headers"], {"x-api-key": SECRET})

    def test_secret_absent_from_outputs(self):
        """AC-4/AC-18: ключ не попадает ни в снапшот, ни в name_map, ни в stdout/stderr, ни в исключения."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "models.json").write_text(json.dumps({"families": FAMILIES}), encoding="utf-8")
            (d / "aa.json").write_text(json.dumps(AA_PAYLOAD), encoding="utf-8")
            (d / "arena.json").write_text(json.dumps(ARENA_RAW), encoding="utf-8")
            env = dict(os.environ, AA_API_KEY=SECRET)
            cmd = [sys.executable, str(ROOT / "skill" / "scripts" / "fetch_scores.py"), "--models", str(d / "models.json"),
                   "--output", str(d / "scores.json"), "--name-map", str(d / "name_map.json"),
                   "--aa-file", str(d / "aa.json"), "--arena-file", str(d / "arena.json")]
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            for blob in (r.stdout, r.stderr, (d / "scores.json").read_text(encoding="utf-8"),
                         (d / "name_map.json").read_text(encoding="utf-8")):
                self.assertNotIn(SECRET, blob)
            # сбой чтения входа: текст ошибки тоже без ключа
            r = subprocess.run(cmd[:3] + [str(d / "nope.json")] + cmd[4:], capture_output=True, text=True, env=env, encoding="utf-8")
            self.assertEqual(r.returncode, 1)
            self.assertNotIn(SECRET, r.stdout + r.stderr)

    def test_scrub(self):
        self.assertEqual(fs.scrub("bad " + SECRET, SECRET), "bad ***")

    def test_gitignore_and_attribution(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("skill/data/scores.json", ignored)
        self.assertIn(".env", ignored)
        out = subprocess.run([sys.executable, str(ROOT / "skill" / "scripts" / "fetch_scores.py"), "--help"],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0)


class AtomicWriteTests(unittest.TestCase):
    def test_total_failure_keeps_previous_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "models.json").write_text(json.dumps({"families": FAMILIES}), encoding="utf-8")
            (d / "aa.json").write_text('{"data": []}', encoding="utf-8")
            (d / "arena.json").write_text("{}", encoding="utf-8")
            (d / "scores.json").write_text('{"old": true}', encoding="utf-8")
            (d / "name_map.json").write_text('{"old": true}', encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "AA_API_KEY"}
            r = subprocess.run([sys.executable, str(ROOT / "skill" / "scripts" / "fetch_scores.py"), "--models", str(d / "models.json"),
                                "--output", str(d / "scores.json"), "--name-map", str(d / "name_map.json"),
                                "--aa-file", str(d / "aa.json"), "--arena-file", str(d / "arena.json")],
                               capture_output=True, text=True, env=env, encoding="utf-8")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads((d / "scores.json").read_text(encoding="utf-8")), {"old": True})
            self.assertEqual(json.loads((d / "name_map.json").read_text(encoding="utf-8")), {"old": True})


class PortabilityTests(unittest.TestCase):
    def test_stdlib_only_imports(self):
        stdlib = set(sys.stdlib_module_names)
        for name in ("fetch_scores.py", "fetch_models.py"):
            tree = ast.parse((ROOT / "skill" / "scripts" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
                for m in mods:
                    self.assertTrue(m.split(".")[0] in stdlib or m.split(".")[0] == "fetch_models", f"{name}: {m}")

    def test_deterministic_output(self):
        a = json.dumps(collect(), sort_keys=True)
        b = json.dumps(collect(), sort_keys=True)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
