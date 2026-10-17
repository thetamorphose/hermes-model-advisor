"""Тесты query.py (AC-5…AC-20, шаги 4 и 5). Сеть не нужна."""
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


def run_cli_files(models, scores, files, *argv, now=NOW):
    """Как run_cli, но с дополнительными файлами в папке данных (name_map.json, descriptions_ru.json)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "models.json").write_text(json.dumps(models), encoding="utf-8")
        (d / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
        for name, content in files.items():
            (d / name).write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        args = q.build_parser().parse_args(["--data-dir", str(d), "--now", now, *argv])
        return "\n".join(q.run(args))


def translation(fid, text="Перевод " , original=None, date="2026-10-16"):
    original = "Описание " + fid if original is None else original
    return {"schema_version": 1, "descriptions": {fid: {"text": text, "source_hash": q.description_hash(original),
                                                       "translated_at": date}}}


class CompareTests(unittest.TestCase):
    def test_ac13_table_with_all_fields(self):  # AC-13
        text = run_cli(*snapshots(), "compare", "a/mid", "b/good-cheap")
        self.assertIn("## Сравнение моделей", text)
        for label in ("Цена вх / исх", "Контекст", "Модальности", "Общий рейтинг", "Программирование",
                      "Агентные задачи", "Мультимодальность"):
            self.assertIn(label, text)
        self.assertIn("40 (Intelligence Index, AA, 2026-10-17)", text)
        self.assertIn("конфигурация: high", text)

    def test_ac13_missing_values_are_no_data(self):  # AC-13
        text = run_cli(*snapshots(), "compare", "a/mid", "d/nodata")
        row = next(l for l in text.split("\n") if l.startswith("| Общий рейтинг"))
        self.assertIn("нет данных", row)
        row = next(l for l in text.split("\n") if l.startswith("| Агентные"))
        self.assertEqual(row.count("нет данных"), 2)
        self.assertNotIn("| |", text)

    def test_nonchat_and_alias_visible_with_flags(self):  # AC-20
        text = run_cli(*snapshots(), "compare", "e/embed", "~f/thing-latest")
        self.assertIn("не-чат модель", text)
        self.assertIn("алиас: версия не определена", text)

    def test_alias_resolves_with_and_without_tilde(self):
        self.assertIn("f/thing-latest", run_cli(*snapshots(), "compare", "~f/thing-latest", "a/mid"))

    def test_mixed_sources_flagged(self):
        self.assertIn("Источники смешаны", run_cli(*snapshots(), "compare", "a/mid", "c/arena-one"))
        self.assertNotIn("Источники смешаны", run_cli(*snapshots(), "compare", "a/mid", "a/top"))

    def test_needs_two_distinct_models(self):
        with self.assertRaises(q.QueryError):
            run_cli(*snapshots(), "compare", "a/mid", "a/mid")

    def test_unknown_model_names_the_problem(self):
        with self.assertRaises(q.QueryError) as ctx:
            run_cli(*snapshots(), "compare", "a/mid", "zzz/none")
        self.assertIn("zzz/none", str(ctx.exception))


class ResolveAndSearchTests(unittest.TestCase):
    def test_resolve_by_id_short_id_and_name(self):
        fams = FAMILIES
        self.assertEqual(q.resolve_model("a/mid", fams)["family_id"], "a/mid")
        self.assertEqual(q.resolve_model("A/MID", fams)["family_id"], "a/mid")
        self.assertEqual(q.resolve_model("good-cheap", fams)["family_id"], "b/good-cheap")

    def test_ambiguous_lists_candidates(self):
        with self.assertRaises(q.QueryError) as ctx:
            q.resolve_model("arena", FAMILIES)
        self.assertIn("c/arena-one", str(ctx.exception))
        self.assertIn("c/arena-two", str(ctx.exception))

    def test_not_found_suggests_close_ids(self):
        with self.assertRaises(q.QueryError) as ctx:
            q.resolve_model("a/mod", FAMILIES)
        self.assertIn("a/mid", str(ctx.exception))

    def test_variant_id_resolves_to_family(self):
        fams = [dict(fam("x/base"), variant_ids=["x/base", "x/base:US"])]
        self.assertEqual(q.resolve_model("x/base:us", fams)["family_id"], "x/base")

    def test_search_output_and_limit(self):
        text = run_cli(*snapshots(), "search", "arena")
        self.assertIn("Найдено: 2, показано: 2.", text)
        self.assertIn("1400 (Arena text overall", text)
        limited = run_cli(*snapshots(), "search", "a/", "--limit", "1")
        self.assertIn("показано: 1", limited)
        self.assertIn("Уточните запрос", limited)

    def test_search_no_hits_says_no_data(self):
        self.assertIn("нет данных", run_cli(*snapshots(), "search", "qqqq"))

    def test_search_is_sorted_by_id(self):
        ids = [f["family_id"] for f in q.search_families(["a/"], FAMILIES)]
        self.assertEqual(ids, sorted(ids))

    def test_search_shows_nonchat_and_alias(self):
        text = run_cli(*snapshots(), "search", "embed")
        self.assertIn("не-чат модель", text)
        self.assertIn("алиас: версия не определена", run_cli(*snapshots(), "search", "thing"))


class ProsConsTests(unittest.TestCase):
    """Пул из тестовых данных мал, поэтому порог размера пула снижен до 3 через конфигурацию."""

    def setUp(self):
        self.cfg = qcfg()
        self.cfg["pros_cons"]["min_pool_size"] = 3
        self.refs = q.reference_stats(FAMILIES, SCORES, self.cfg)

    def pc(self, fid):
        fam_ = next(f for f in FAMILIES if f["family_id"] == fid)
        return q.pros_cons(fam_, SCORES, self.refs, self.cfg)

    def test_ac14_top_and_below_median_from_numbers(self):  # AC-14
        pros, cons = self.pc("a/top")
        self.assertTrue(any("в верхних 25% по Intelligence Index (AA)" in t for t in pros))
        pros, cons = self.pc("a/cheap-weak")
        self.assertTrue(any("ниже медианы по Intelligence Index (AA)" in t for t in cons))

    def test_no_vision_and_image_input(self):
        self.assertIn("нет vision (нет image на входе)", self.pc("a/mid")[1])
        self.assertIn("принимает изображения на входе", self.pc("b/ok")[0])

    def test_no_scores_is_a_con_not_zero(self):
        self.assertIn("нет оценок ни в одном бенчмарке", self.pc("d/nodata")[1])

    def test_context_rules_from_config(self):
        fam_ = dict(fam("x/long"), context_length=2_000_000)
        self.assertTrue(any("контекст ≥ 1M" in t for t in q.pros_cons(fam_, {}, self.refs, self.cfg)[0]))
        fam_["context_length"] = 8000
        self.assertTrue(any("короткий контекст" in t for t in q.pros_cons(fam_, {}, self.refs, self.cfg)[1]))

    def test_expensive_output_rule(self):
        self.assertTrue(any("дорогой output" in t for t in self.pc("a/top")[1]))
        self.assertTrue(any("дешёвый output" in t for t in self.pc("a/cheap-weak")[0]))

    def test_thresholds_come_from_config(self):
        self.cfg["pros_cons"]["long_context_tokens"] = 100_000
        refs = q.reference_stats(FAMILIES, SCORES, self.cfg)
        pros, _ = q.pros_cons(FAMILIES[1], SCORES, refs, self.cfg)  # контекст 128K
        self.assertTrue(any("контекст ≥ 100K" in t for t in pros))

    def test_small_pool_gives_no_score_rules(self):
        cfg = qcfg()  # min_pool_size 10 > размера тестового пула
        refs = q.reference_stats(FAMILIES, SCORES, cfg)
        pros, cons = q.pros_cons(FAMILIES[2], SCORES, refs, cfg)
        self.assertFalse(any("Intelligence Index" in t for t in pros + cons))

    def test_reference_pool_excludes_alias_and_nonchat(self):
        refs = q.reference_stats(FAMILIES, SCORES, self.cfg)
        self.assertEqual(refs["metrics"]["overall"]["artificial_analysis"]["count"], 7)  # без embed и алиаса

    def test_pros_cons_are_deterministic(self):
        self.assertEqual(self.pc("a/top"), self.pc("a/top"))


class ShowTests(unittest.TestCase):
    def test_ac14_card_sections(self):  # AC-14
        text = run_cli(*snapshots(), "show", "b/good-cheap")
        for part in ("## Справка: b/good-cheap", "- Цена вх / исх, $/1M: $0.5 / $0.5", "- Контекст: 128K",
                     "вход: text, image", "### Оценки", "60 (Intelligence Index)", "AA, 2026-10-17, конфигурация: high",
                     "Arena, 2026-10-16", "### Плюсы", "### Минусы", "### Описание"):
            self.assertIn(part, text)
        self.assertIn("Значения разных источников", text)

    def test_ac12_arena_only_card(self):  # AC-12
        text = run_cli(*snapshots(), "show", "c/arena-one")
        self.assertIn("| Общий рейтинг | 1400 (Arena text overall) | Arena, 2026-10-16 |", text)

    def test_missing_scores_show_no_data(self):
        text = run_cli(*snapshots(), "show", "d/nodata")
        self.assertEqual(text.count("| нет данных | — |"), 4)

    def test_nonchat_and_alias_cards(self):  # AC-20
        self.assertIn("не-чат модель", run_cli(*snapshots(), "show", "e/embed"))
        self.assertIn("алиас: версия не определена", run_cli(*snapshots(), "show", "~f/thing-latest"))

    def test_price_variant_note(self):
        models, scores = snapshots()
        fams = [dict(f) for f in models["families"]]
        fams[1]["price_variants"] = [{"id": "a/mid:US", "prompt": 2.0, "completion": 3.0}]
        models = dict(models, families=fams)
        self.assertIn("вариант US: $2/$3", run_cli(models, scores, "show", "a/mid"))

    def test_header_first(self):
        self.assertTrue(run_cli(*snapshots(), "show", "a/mid").startswith("# Советник по моделям Nous\nКаталог:"))

    def test_fallback_mode_show_works(self):
        models, scores = snapshots(aa_status={"status": "unavailable", "reason": "no_key"}, mode="fallback")
        scores["models"] = {k: {m: (v if v and v["source"] == "lmarena" else None) for m, v in s.items()}
                            for k, s in SCORES.items()}
        text = run_cli(models, scores, "show", "c/arena-one")
        self.assertIn("не задан AA_API_KEY", text)
        self.assertIn("1400", text)


class TranslationTests(unittest.TestCase):
    def test_ac15_translation_marked_with_label_and_date(self):  # AC-15
        text = run_cli_files(*snapshots(), {"descriptions_ru.json": translation("a/mid", "Русское описание")},
                             "show", "a/mid")
        self.assertIn("Русское описание [машинный перевод, 2026-10-16]", text)
        self.assertNotIn("Описание a/mid", text)

    def test_ac15_changed_original_shows_original(self):  # AC-15
        files = {"descriptions_ru.json": translation("a/mid", "Старый перевод", original="Прежний оригинал")}
        text = run_cli_files(*snapshots(), files, "show", "a/mid")
        self.assertIn("Описание a/mid [оригинал, перевод устарел]", text)
        self.assertNotIn("Старый перевод", text)
        self.assertIn("Оригинал описания изменился после перевода: 1", text)

    def test_ac15_no_translation_shows_original(self):  # AC-15
        text = run_cli(*snapshots(), "show", "a/mid")
        self.assertIn("Описание a/mid [оригинал]", text)

    def test_translation_used_in_models_sets(self):
        files = {"descriptions_ru.json": translation("a/top", "Перевод флагмана")}
        text = run_cli_files(*snapshots(), files, "models", "--set", "flagships")
        self.assertIn("Перевод флагмана [машинный перевод, 2026-10-16]", text)
        self.assertIn("Описание b/good-cheap [оригинал]", text)

    def test_hash_ignores_whitespace_only_changes(self):
        self.assertEqual(q.description_hash("a  b\n c"), q.description_hash("a b c"))
        self.assertNotEqual(q.description_hash("a b"), q.description_hash("a c"))

    def test_malformed_entry_falls_back_to_original(self):
        files = {"descriptions_ru.json": {"schema_version": 1, "descriptions": {"a/mid": {"text": "x"}}}}
        self.assertIn("Описание a/mid [оригинал]", run_cli_files(*snapshots(), files, "show", "a/mid"))

    def test_truncation_in_lists_but_not_in_card(self):
        long_text = "слово " * 100
        models, scores = snapshots()
        fams = [dict(f) for f in models["families"]]
        fams[2]["description"] = long_text
        models = dict(models, families=fams)
        files = {"descriptions_ru.json": translation("a/top", long_text, original=long_text)}
        self.assertIn("…", run_cli_files(models, scores, files, "models", "--set", "flagships"))
        self.assertNotIn("…", run_cli_files(models, scores, files, "show", "a/top"))


class IntegrityTests(unittest.TestCase):
    def name_map(self, *ids):
        return {"schema_version": 1, "matches": {i: {} for i in ids}, "candidates": [], "ambiguous_catalog_keys": 0}

    def test_ac16_stale_ids_flagged_not_deleted(self):  # AC-16
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            models, scores = snapshots()
            (d / "models.json").write_text(json.dumps(models), encoding="utf-8")
            (d / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
            nm = self.name_map("a/mid", "gone/model")
            tr = translation("old/model", "Перевод")
            (d / "name_map.json").write_text(json.dumps(nm), encoding="utf-8")
            (d / "descriptions_ru.json").write_text(json.dumps(tr), encoding="utf-8")
            before = {n: (d / n).read_bytes() for n in ("name_map.json", "descriptions_ru.json")}
            args = q.build_parser().parse_args(["--data-dir", str(d), "--now", NOW, "search", "mid"])
            text = "\n".join(q.run(args))
            self.assertIn("Устарели записи name_map.json", text)
            self.assertIn("`gone/model`", text)
            self.assertIn("Устарели переводы descriptions_ru.json", text)
            self.assertIn("`old/model`", text)
            self.assertEqual(before, {n: (d / n).read_bytes() for n in before})

    def test_flagged_in_every_command(self):  # AC-16
        files = {"name_map.json": self.name_map("gone/model")}
        for argv in (("search", "mid"), ("show", "a/mid"), ("compare", "a/mid", "a/top"), ("models",),
                     ("recommend", "--task", "coding")):
            self.assertIn("`gone/model`", run_cli_files(*snapshots(), files, *argv), argv)

    def test_current_ids_are_not_flagged(self):
        files = {"name_map.json": self.name_map("a/mid"), "descriptions_ru.json": translation("a/mid")}
        self.assertNotIn("⚠", run_cli_files(*snapshots(), files, "search", "mid"))

    def test_candidate_ids_checked_too(self):
        nm = {"schema_version": 1, "matches": {}, "candidates": [{"family_id": "gone/cand"}]}
        self.assertIn("`gone/cand`", run_cli_files(*snapshots(), {"name_map.json": nm}, "search", "mid"))

    def test_broken_optional_files_warn_instead_of_crash(self):
        files = {"name_map.json": "{not json", "descriptions_ru.json": json.dumps({"schema_version": 9})}
        text = run_cli_files(*snapshots(), files, "search", "mid")
        self.assertIn("name_map.json: файл не удалось прочитать", text)
        self.assertIn("descriptions_ru.json: schema_version 9", text)

    def test_missing_optional_files_are_silent(self):
        self.assertNotIn("⚠", run_cli(*snapshots(), "search", "mid"))


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
        for argv in (("models",), ("recommend", "--task", "coding"), ("show", "b/good-cheap"),
                     ("compare", "a/mid", "c/arena-one"), ("search", "a/")):
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
