"""Тесты fetch_models.py (AC-1, AC-2, AgDR-0001). Запуск: python -m unittest discover -s tests -v"""
import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import fetch_models as fm  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "nous_models_sample.json").read_text(encoding="utf-8"))


class PriceTests(unittest.TestCase):
    def test_per_million_has_no_float_noise(self):
        self.assertEqual(fm.per_million("0.0000009400"), 0.94)
        self.assertEqual(fm.per_million("0.0000018900"), 1.89)
        self.assertEqual(fm.per_million("0.0000000200"), 0.02)
        self.assertEqual(fm.per_million("0"), 0.0)

    def test_bad_price_is_none_not_zero(self):
        self.assertIsNone(fm.per_million(None))
        self.assertIsNone(fm.per_million("abc"))

    def test_list_price_and_tiers_kept(self):
        fam = self._fam("vendor/disc")
        self.assertEqual(fam["pricing_per_1m"]["prompt"], 1.0)
        self.assertEqual(fam["pricing_per_1m"]["list_price"]["prompt"], 2.0)
        self.assertEqual(fam["pricing_per_1m"]["tiers"][0]["min_prompt_tokens"], 100000)

    def _fam(self, key):
        snap = fm.build_snapshot(FIXTURE, "2026-10-17T00:00:00Z")
        return {f["family_id"]: f for f in snap["families"]}[key]


class FamilyTests(unittest.TestCase):
    def setUp(self):
        self.snap = fm.build_snapshot(FIXTURE, "2026-10-17T00:00:00Z")
        self.fam = {f["family_id"]: f for f in self.snap["families"]}

    def test_every_record_in_exactly_one_family(self):
        ids = [i for f in self.snap["families"] for i in f["variant_ids"]]
        self.assertEqual(sorted(ids), sorted(r["id"] for r in FIXTURE["data"]))
        self.assertEqual(self.snap["count"], len(FIXTURE["data"]))

    def test_representative_is_base_variant_and_price_variants_flagged(self):
        a = self.fam["vendor/alpha"]
        self.assertEqual(a["representative_id"], "vendor/alpha")
        self.assertEqual(a["pricing_per_1m"]["prompt"], 0.94)
        self.assertEqual(sorted(v["id"] for v in a["price_variants"]), ["vendor/alpha-0813", "vendor/alpha:batch"])

    def test_dated_revision_merged_only_if_base_exists(self):
        self.assertIn("vendor/alpha-0813", self.fam["vendor/alpha"]["variant_ids"])
        self.assertIn("vendor/beta-2603", self.fam)  # базы vendor/beta нет: дата не срезается
        self.assertNotIn("vendor/beta", self.fam)

    def test_alias_is_separate_family_marked_alias(self):
        g = self.fam["vendor/gamma-latest"]
        self.assertTrue(g["alias"])
        self.assertEqual(g["representative_id"], "~vendor/gamma-latest")

    def test_latest_with_dated_slug_is_not_alias(self):
        self.assertFalse(self.fam["vendor/chat-latest"]["alias"])

    def test_embedding_marked_not_chat(self):
        self.assertIs(self.fam["vendor/embed-1"]["chat"], False)
        self.assertIs(self.fam["vendor/alpha"]["chat"], True)

    def test_empty_architecture_borrows_modalities_from_sibling(self):
        d = self.fam["vendor/delta"]
        self.assertEqual(d["modalities"]["input"], ["text", "image", "file"])
        self.assertEqual(d["modalities_from"], "vendor/delta:batch")
        self.assertIs(d["chat"], True)

    def test_unknown_modalities_give_chat_none_not_false(self):
        rec = {"id": "x/y", "canonical_slug": "x/y", "pricing": {"prompt": "0.000001", "completion": "0.000001"}, "architecture": {}}
        fam = fm.build_families([rec])[0]
        self.assertIsNone(fam["modalities"])
        self.assertIsNone(fam["chat"])

    def test_free_only_family_represented_by_free_variant(self):
        self.assertEqual(self.fam["vendor/free-only"]["representative_id"], "vendor/free-only:free")

    def test_deterministic(self):
        again = fm.build_snapshot(FIXTURE, "2026-10-17T00:00:00Z")
        self.assertEqual(json.dumps(self.snap, sort_keys=True), json.dumps(again, sort_keys=True))


class TokenTests(unittest.TestCase):
    def test_priority_cli_env_authjson(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "hermes" / "auth.json"
            auth.parent.mkdir()
            auth.write_text(json.dumps({"providers": {"nous": {"agent_key": "FILE"}}}), encoding="utf-8")
            env = {"LOCALAPPDATA": tmp}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(Path, "home", return_value=Path(tmp) / "nohome"):
                self.assertEqual(fm.find_token(None), "FILE")
                with mock.patch.dict(os.environ, {"NOUS_API_TOKEN": "ENV"}):
                    self.assertEqual(fm.find_token(None), "ENV")
                    self.assertEqual(fm.find_token("CLI"), "CLI")

    def test_no_token_is_allowed(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(Path, "home", return_value=Path("/nonexistent-home")):
            self.assertIsNone(fm.find_token(None))


class CliTests(unittest.TestCase):
    def test_from_file_writes_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "models.json"
            rc = fm.main(["--from-file", str(ROOT / "tests/fixtures/nous_models_sample.json"), "--output", str(out)])
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["count"], len(FIXTURE["data"]))

    def test_failure_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "models.json"
            out.write_text('{"old": true}', encoding="utf-8")
            bad = Path(tmp) / "bad.json"
            bad.write_text('{"data": []}', encoding="utf-8")
            rc = fm.main(["--from-file", str(bad), "--output", str(out)])
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), {"old": True})


class PortabilityTests(unittest.TestCase):
    def test_only_stdlib_imports(self):
        tree = ast.parse((ROOT / "skill" / "scripts" / "fetch_models.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split(".")[0])
        self.assertLessEqual(names, set(sys.stdlib_module_names) | {"__future__"})


if __name__ == "__main__":
    unittest.main()
