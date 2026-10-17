"""Тесты query.py (AC-5…AC-12, AC-17, AC-18, AC-19, AC-20). Сеть не нужна."""
import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import query as q  # noqa: E402

CONFIG = ROOT / "skill" / "config.json"
NOW = "2026-10-17T12:00:00Z"
FETCHED = "2026-10-17T06:00:00Z"
CREATED_RECENT = 1792000000  # 2026-10-14
CREATED_OLD = 1700000000     # 2023


def fam(fid, prompt=1.0, completion=1.0, created=CREATED_OLD, alias=False, chat=True, inputs=("text",),
        variants=None, extra_pricing=None):
    pricing = {"prompt": prompt, "completion": completion, **(extra_pricing or {})}
    return {"family_id": fid, "representative_id": fid, "variant_ids": [fid], "alias": alias, "name": fid,
            "created": created, "context_length": 128000, "chat": chat,
            "modalities": {"input": list(inputs), "output": ["text"] if chat else ["embeddings"]},
            "pricing_per_1m": pricing, "price_variants": variants or [], "description": "Описание " + fid}


def aa(v, metric="Intelligence Index", config=None):
    return {"value": v, "source": "artificial_analysis", "metric": metric, "date": "2026-10-17",
            "config": config, "matched_name": "x"}


def arena(v, metric="Arena text overall", config=None):
    return {"value": v, "source": "lmarena", "metric": metric, "date": "2026-10-16",
            "config": config, "matched_name": "x"}


def blank(**kw):
    out = {"overall": None, "coding": None, "agentic": None, "vision": None}
    out.update(kw)
    return out


FAMILIES = [
    fam("a/cheap-weak", 0.1, 0.1, CREATED_RECENT),
    fam("a/mid", 1.0, 1.0),
    fam("a/top", 10.0, 10.0),
    fam("b/good-cheap", 0.5, 0.5, CREATED_RECENT, inputs=("text", "image")),
    fam("b/ok", 2.0, 2.0, inputs=("text", "image")),
    fam("c/arena-one", 1.0, 1.0),
    fam("c/arena-two", 0.4, 0.4),
    fam("d/nodata", 1.0, 1.0),
    fam("e/embed", 0.01, 0.0, chat=False),
    fam("~f/thing-latest", 1.0, 1.0, alias=True),
    fam("g/free", 0.0, 0.0),
    fam("h/noprice", 1.0, 1.0),
]
FAMILIES[-1]["pricing_per_1m"] = {}

SCORES = {
    "a/cheap-weak": blank(overall=aa(10), coding=aa(10, "Coding Index")),
    "a/mid": blank(overall=aa(40), coding=aa(40, "Coding Index")),
    "a/top": blank(overall=aa(80), coding=aa(80, "Coding Index")),
    "b/good-cheap": blank(overall=aa(60, config="high"), coding=aa(70, "Coding Index"),
                          vision=arena(1300, "Arena vision overall")),
    "b/ok": blank(overall=aa(50), coding=aa(50, "Coding Index"), vision=arena(1200, "Arena vision overall")),
    "c/arena-one": blank(overall=arena(1400), coding=arena(1400, "Arena text coding")),
    "c/arena-two": blank(overall=arena(1300), coding=arena(1300, "Arena text coding")),
    "e/embed": blank(overall=aa(99)),
    "~f/thing-latest": blank(overall=aa(99)),
    "g/free": blank(overall=aa(70)),
    "h/noprice": blank(overall=aa(75)),
}


def snapshots(aa_status=None, arena_status=None, mode="full", fetched=FETCHED, scores_at="2026-10-17T06:30:00Z"):
    models = {"schema_version": 1, "fetched_at": fetched, "family_count": len(FAMILIES), "families": FAMILIES}
    scores = {"schema_version": 1, "fetched_at": scores_at, "mode": mode,
              "sources": {"artificial_analysis": aa_status or {"status": "ok"},
                          "lmarena": arena_status or {"status": "ok", "leaderboard_date": "2026-10-16"}},
              "models": SCORES}
    return models, scores


def qcfg():
    return json.loads(CONFIG.read_text(encoding="utf-8"))["query"]


def run_cli(models, scores, *argv, now=NOW):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "models.json").write_text(json.dumps(models), encoding="utf-8")
        (d / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
        args = q.build_parser().parse_args(["--data-dir", str(d), "--now", now, *argv])
        return "\n".join(q.run(args))


class MathTests(unittest.TestCase):
    def test_mixed_price(self):
        self.assertEqual(q.mixed_price({"prompt": 2, "completion": 4}, {"in": 3, "out": 1}), 10)
        self.assertIsNone(q.mixed_price({"prompt": 2}, {"in": 3, "out": 1}))

    def test_percentile_median(self):
        self.assertEqual(q.percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertEqual(q.percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(q.percentile([7], 50), 7)

    def test_normalization_per_source(self):
        pool = [{"source": "a", "raw": 10}, {"source": "a", "raw": 20}, {"source": "b", "raw": 1000},
                {"source": "b", "raw": 1100}]
        q.annotate_norm(pool, 50)
        self.assertEqual([e["norm"] for e in pool], [0.0, 100.0, 0.0, 100.0])
        self.assertEqual([e["passes"] for e in pool], [False, True, False, True])

    def test_single_value_source_passes(self):
        pool = [{"source": "a", "raw": 5}]
        q.annotate_norm(pool, 50)
        self.assertTrue(pool[0]["passes"])
        self.assertEqual(pool[0]["norm"], 100.0)


class RecommendTests(unittest.TestCase):
    def test_ac6_direct_category(self):  # AC-6
        res = q.recommend("coding", FAMILIES, SCORES, qcfg(), 20)
        ids = [e["family"]["family_id"] for e in res["shown"]]
        self.assertTrue(res["direct"])
        self.assertNotIn("a/cheap-weak", ids)  # дешёвая, но ниже медианы своего источника
        self.assertNotIn("a/mid", ids)         # AA: 40 при медиане 50
        self.assertIn("b/good-cheap", ids)
        values = [e["value"] for e in res["shown"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_ac6_threshold_is_per_source(self):
        res = q.recommend("coding", FAMILIES, SCORES, qcfg(), 20)
        self.assertEqual(set(res["stats"]), {"artificial_analysis", "lmarena"})
        ids = [e["family"]["family_id"] for e in res["shown"]]
        self.assertIn("c/arena-one", ids)      # 1400 выше медианы Arena 1350
        self.assertNotIn("c/arena-two", ids)

    def test_ac7_overall_category_labels(self):  # AC-7
        text = run_cli(*snapshots(), "recommend", "--task", "business_spec")
        self.assertIn("Оценка по общему рейтингу", text)
        self.assertIn("| Общий рейтинг |", text)
        self.assertNotIn("| Качество |", text)
        rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
        self.assertTrue(rows)
        for line in rows:
            self.assertIn("оценка по общему рейтингу", line)

    def test_reasoning_is_overall(self):
        text = run_cli(*snapshots(), "recommend", "--task", "reasoning")
        self.assertIn("| Общий рейтинг |", text)

    def test_direct_category_column(self):
        text = run_cli(*snapshots(), "recommend", "--task", "coding")
        self.assertIn("| Качество |", text)
        self.assertNotIn("оценка по общему рейтингу", text)

    def test_ac8_budget_ranks_by_price(self):  # AC-8
        res = q.recommend("budget", FAMILIES, SCORES, qcfg(), 20)
        prices = [e["mixed"] for e in res["shown"]]
        self.assertEqual(prices, sorted(prices))
        text = run_cli(*snapshots(), "recommend", "--task", "budget")
        self.assertNotIn("| Value |", text)
        self.assertIn("value не применяется", text)

    def test_ac9_mixed_sources(self):  # AC-9
        text = run_cli(*snapshots(), "recommend", "--task", "coding")
        self.assertIn("Источники смешаны", text)

    def test_single_source_not_flagged_mixed(self):
        res = q.recommend("vision", FAMILIES, SCORES, qcfg(), 20)
        self.assertFalse(res["mixed_sources"])

    def test_vision_requires_image_input(self):
        res = q.recommend("vision", FAMILIES, SCORES, qcfg(), 20)
        self.assertLessEqual({e["family"]["family_id"] for e in res["shown"]}, {"b/good-cheap", "b/ok"})

    def test_ac20_alias_and_nonchat_excluded(self):  # AC-20
        res = q.recommend("business_spec", FAMILIES, SCORES, qcfg(), 50)
        ids = {e["family"]["family_id"] for e in res["shown"]}
        self.assertFalse(ids & {"e/embed", "~f/thing-latest"})
        self.assertEqual(res["skipped"]["alias"], 1)
        self.assertEqual(res["skipped"]["non_chat"], 1)

    def test_config_label_on_value(self):
        text = run_cli(*snapshots(), "models", "--set", "flagships")
        line = next(ln for ln in text.splitlines() if "Общий рейтинг: 60" in ln)
        self.assertIn("(Intelligence Index, AA, 2026-10-17), конфигурация: high", line)
        self.assertEqual(text.count("конфигурация: "), 1)  # у Medium и у единственной конфигурации пометки нет

    def test_free_model_handled(self):
        res = q.recommend("business_spec", FAMILIES, SCORES, qcfg(), 50)
        free = [e for e in res["shown"] if e["family"]["family_id"] == "g/free"]
        self.assertTrue(free and free[0]["free"])

    def test_model_without_price_not_ranked_but_counted(self):
        res = q.recommend("business_spec", FAMILIES, SCORES, qcfg(), 50)
        self.assertNotIn("h/noprice", {e["family"]["family_id"] for e in res["shown"]})
        self.assertEqual(res["unpriced"], 1)

    def test_price_notes(self):
        f = fam("x/y", variants=[{"id": "x/y:batch", "prompt": 0.5, "completion": 1.0}],
                extra_pricing={"list_price": {"prompt": 2.0, "completion": 2.0}})
        notes = q.price_notes(f)
        self.assertTrue(any("batch" in n for n in notes))
        self.assertTrue(any("скидка 50%" in n for n in notes))

    def test_unknown_task(self):
        with self.assertRaises(q.QueryError):
            run_cli(*snapshots(), "recommend", "--task", "nope")

    def test_empty_pool_says_no_data(self):
        text = run_cli(*snapshots(), "recommend", "--task", "agentic")
        self.assertIn("нет данных", text)


class FlagshipTests(unittest.TestCase):
    def setUp(self):
        self.res = q.flagships(FAMILIES, SCORES, qcfg())
        self.ids = [e["family"]["family_id"] for e in self.res["rows"]]

    def test_ac10_one_per_developer_aa_first(self):  # AC-10
        devs = [q.developer(e["family"]) for e in self.res["rows"]]
        self.assertEqual(len(devs), len(set(devs)))
        sources = [e["source"] for e in self.res["rows"]]
        self.assertEqual(sources, sorted(sources, key=q.SOURCE_ORDER.index))
        self.assertEqual(self.ids[-1], "c/arena-one")  # Arena - после всех флагманов с AA

    def test_flagship_is_max_overall_ignoring_price(self):
        self.assertIn("a/top", self.ids)       # самая дорогая, но с максимальным рейтингом
        self.assertIn("b/good-cheap", self.ids)

    def test_aa_priority_over_higher_arena_value(self):
        fams = [fam("z/aa-low"), fam("z/arena-high")]
        scores = {"z/aa-low": blank(overall=aa(5)), "z/arena-high": blank(overall=arena(1500))}
        res = q.flagships(fams, scores, qcfg())
        self.assertEqual([e["family"]["family_id"] for e in res["rows"]], ["z/aa-low"])

    def test_no_data_developers_listed(self):
        self.assertEqual(self.res["no_data"], ["d"])  # embed и алиас разработчиков без данных не создают

    def test_flagship_may_lack_price(self):
        self.assertIn("h/noprice", self.ids)
        text = run_cli(*snapshots(), "models", "--set", "flagships")
        line = next(ln for ln in text.splitlines() if "Общий рейтинг: 75" in ln)
        self.assertIn("value: нет данных", line)

    def test_rule_in_output_and_price_next_to_flagship(self):
        text = run_cli(*snapshots(), "models", "--set", "flagships")
        self.assertIn(qcfg()["flagship_rule"], text)
        self.assertIn("цена вх / исх", text)
        self.assertIn("value", text)
        self.assertIn("Нет данных для определения флагмана", text)

    def test_ac12_arena_only_shows_value_with_source(self):  # AC-12
        text = run_cli(*snapshots(), "models", "--set", "flagships")
        row = next(ln for ln in text.splitlines() if "Общий рейтинг: 1400" in ln)
        self.assertIn("Arena text overall, Arena", row)


class SpotlightTests(unittest.TestCase):
    def test_ac11_fresh_without_old_alias_nonchat(self):  # AC-11
        ref = q.parse_time(FETCHED).timestamp()
        res = q.spotlight(FAMILIES, SCORES, qcfg(), ref)
        ids = [f["family_id"] for f in res["fresh"]]
        self.assertEqual(set(ids), {"a/cheap-weak", "b/good-cheap"})
        self.assertEqual(len(ids), len(set(ids)))

    def test_cheap_block_above_threshold_only(self):
        ref = q.parse_time(FETCHED).timestamp()
        res = q.spotlight(FAMILIES, SCORES, qcfg(), ref)
        ids = {e["family"]["family_id"] for e in res["cheap"]}
        self.assertNotIn("a/cheap-weak", ids)  # дешёвая, но ниже порога качества
        self.assertTrue(ids)

    def test_overlap_marked_in_both_lists(self):
        text = run_cli(*snapshots(), "models")
        self.assertIn("также в spotlight", text)
        self.assertIn("также флагман", text)
        self.assertIn("Релиз:", text)
        self.assertLess(text.index("## Флагманы"), text.index("## Spotlight"))

    def test_thresholds_come_from_config(self):
        cfg = qcfg()
        cfg["spotlight"]["fresh_days"] = 1
        ref = q.parse_time(FETCHED).timestamp()
        self.assertEqual(q.spotlight(FAMILIES, SCORES, cfg, ref)["fresh"], [])


class HeaderTests(unittest.TestCase):
    def test_ac5_header_first_with_dates_and_age(self):  # AC-5
        lines = run_cli(*snapshots(), "recommend", "--task", "coding").splitlines()
        self.assertTrue(lines[0].startswith("#"))
        self.assertIn("Каталог: 2026-10-17 06:00 UTC (сегодня)", lines[1])
        self.assertIn("Оценки: 2026-10-17 06:30 UTC", lines[2])
        self.assertNotIn("Обновите", "\n".join(lines))

    def test_ac5_stale_hint(self):
        text = run_cli(*snapshots(fetched="2026-10-01T00:00:00Z"), "recommend", "--task", "coding")
        self.assertIn("16 дн. назад", text)
        self.assertIn("fetch_models.py", text)
        self.assertIn("fetch_scores.py", text)

    def test_ac5_aa_unavailable_reason_from_snapshot(self):
        models, scores = snapshots(aa_status={"status": "unavailable", "reason": "rate_limited"}, mode="fallback")
        text = run_cli(models, scores, "recommend", "--task", "coding")
        self.assertIn("AA: нет данных (превышен лимит запросов); оценки — из LMArena", text)

    def test_unknown_reason_code_is_sanitized(self):
        models, scores = snapshots(aa_status={"status": "unavailable", "reason": "sk-SECRET key here"})
        text = run_cli(models, scores, "recommend", "--task", "coding")
        self.assertNotIn("SECRET", text)
        self.assertIn("причина не указана", text)

    def test_partial_arena_reported(self):
        models, scores = snapshots(arena_status={"status": "partial", "leaderboard_date": "2026-10-16",
                                                 "incomplete": {"vision": "bad_response"}})
        text = run_cli(models, scores, "recommend", "--task", "coding")
        self.assertIn("LMArena: данные неполные (vision: bad_response)", text)

    def test_arena_unavailable_reported(self):
        models, scores = snapshots(arena_status={"status": "unavailable", "reason": "network"})
        text = run_cli(models, scores, "recommend", "--task", "coding")
        self.assertIn("LMArena: нет данных (нет сети или таймаут); оценки — только из AA", text)
        self.assertNotIn("CC-BY-4.0", text)

    def test_ac18_attribution(self):  # AC-18
        self.assertIn("CC-BY-4.0", run_cli(*snapshots(), "models"))

    def test_fallback_mode_works(self):  # резервный режим: все рейтинги из Arena
        names = ["a/mid", "a/top", "b/ok", "c/arena-one", "c/arena-two"]
        scores = {k: blank(overall=arena(1000 + i * 10), coding=arena(1000 + i * 10, "Arena text coding"))
                  for i, k in enumerate(names)}
        models, snap = snapshots(aa_status={"status": "unavailable", "reason": "no_key"}, mode="fallback")
        snap["models"] = scores
        for cmd in (("recommend", "--task", "coding"), ("models", "--set", "flagships")):
            text = run_cli(models, snap, *cmd)
            self.assertIn("не задан AA_API_KEY", text)
            self.assertNotIn("Источники смешаны", text)


class ErrorAndDeterminismTests(unittest.TestCase):
    def test_missing_snapshot_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = q.build_parser().parse_args(["--data-dir", tmp, "models"])
            with self.assertRaises(q.QueryError) as ctx:
                q.run(args)
            self.assertIn("fetch_models.py", str(ctx.exception))

    def test_wrong_schema_version(self):
        models, scores = snapshots()
        scores["schema_version"] = 2
        with self.assertRaises(q.QueryError):
            run_cli(models, scores, "models")

    def test_ac17_byte_identical(self):  # AC-17
        for argv in (("models",), ("recommend", "--task", "coding")):
            a = run_cli(*snapshots(), *argv)
            b = run_cli(*snapshots(), *argv)
            self.assertEqual(a.encode("utf-8"), b.encode("utf-8"))

    def test_cli_exit_codes_and_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            models, scores = snapshots()
            (d / "models.json").write_text(json.dumps(models), encoding="utf-8")
            (d / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
            script = str(ROOT / "skill" / "scripts" / "query.py")
            base = [sys.executable, script, "--data-dir", str(d), "--now", NOW]
            ok = subprocess.run(base + ["recommend", "--task", "coding"], capture_output=True)
            self.assertEqual(ok.returncode, 0)
            self.assertIn("Подбор под задачу".encode("utf-8"), ok.stdout)
            self.assertNotIn(b"\r\n", ok.stdout)
            bad = subprocess.run(base + ["recommend", "--task", "x"], capture_output=True)
            self.assertEqual(bad.returncode, 2)
            self.assertNotIn(b"Traceback", bad.stderr)

    def test_config_has_all_categories_with_ratios(self):
        cats = qcfg()["categories"]
        self.assertEqual(len(cats), 11)
        self.assertEqual(cats["coding"]["ratio"], {"in": 3, "out": 1})
        self.assertEqual(cats["creative"]["ratio"], {"in": 1, "out": 2})
        self.assertEqual(cats["budget"]["rank_by"], "price")

    def test_stdlib_only(self):  # AC-19
        stdlib = set(sys.stdlib_module_names)
        tree = ast.parse((ROOT / "skill" / "scripts" / "query.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            for m in mods:
                self.assertIn(m.split(".")[0], stdlib)


if __name__ == "__main__":
    unittest.main()
