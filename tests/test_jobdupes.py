"""jobdupes must give the same keys, fingerprints and cross-listings as both upstream tools."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import jobdupes  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
with open(os.path.join(ROOT, "tests", "upstream.json"), encoding="utf-8") as f:
    UP = json.load(f)
ROWS = jobdupes.load_postings(os.path.join(FIX, "postings.jsonl"))


def run(*args):
    p = subprocess.run([sys.executable, "jobdupes.py", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8")
    return p.returncode, p.stdout


class MatchesUpstream(unittest.TestCase):
    def test_keys_match_ai_job_search(self):
        self.assertEqual([jobdupes.make_key(r["company"], r["title"], r["url"]) for r in ROWS], UP["keys"])

    def test_fingerprints_match_career_ops(self):
        self.assertEqual([jobdupes.fingerprint(r["description"]) for r in ROWS], UP["fingerprints"])

    def test_cross_listings_match_career_ops(self):
        items = [dict(r, fingerprint=fp, i=i) for i, (r, fp) in enumerate(zip(ROWS, UP["fingerprints"]))]
        got = [[o["i"], h["i"], s] for o, h, s in jobdupes.cross_listings(items, items, today="2026-10-20")]
        self.assertEqual(got, UP["cross"])

    def test_audit_matches_ai_job_search(self):
        code, out = run("--audit", os.path.join(FIX, "seen_jobs.json"))
        self.assertEqual((code, json.loads(out)), (UP["audit_exit"], UP["audit"]))


class Behaviour(unittest.TestCase):
    def test_known_key_cases(self):
        self.assertEqual(jobdupes.make_key("Ops Consulting, LLC", "Malware Analyst"), "ops-consulting-llc_malware-analyst")
        a = jobdupes.make_key("Securion", "Программист 1С", "https://hh.example/vacancy/12345678")
        b = jobdupes.make_key("Securion", "Аналитик 1С", "https://hh.example/vacancy/87654321")
        self.assertNotEqual(a, b)  # both fold to "1"; the portal id keeps them apart
        self.assertEqual(jobdupes.make_key("Ørsted", "Data Engineer"), "rsted_data-engineer")

    def test_company_key_folds_turkish_dotted_i_only(self):
        self.assertEqual(jobdupes.text_key("İstanbul Tekstil"), jobdupes.text_key("Istanbul Tekstil"))
        self.assertNotEqual(jobdupes.text_key("Żubr"), jobdupes.text_key("Zubr"))  # the Polish dot stays

    def test_short_or_tokenless_bodies_get_no_fingerprint(self):
        self.assertEqual(jobdupes.fingerprint("short text"), "")
        self.assertEqual(jobdupes.fingerprint("字" * 300), "")  # one giant token: never fingerprinted

    def test_report_groups_every_kind_once(self):
        code, out = run(os.path.join(FIX, "postings.jsonl"), "--today", "2026-10-20", "--json")
        rep = json.loads(out)
        self.assertEqual(code, 0)
        pairs = {tuple(sorted(p["rows"])) for p in rep["cross_listed"]}
        self.assertEqual(len(pairs), len(rep["cross_listed"]))  # mirrored pairs folded
        want = {tuple(sorted(p[:2])) for p in UP["cross"]}
        self.assertEqual(pairs, want)
        self.assertTrue(rep["same_key"] and rep["same_url"])

    def test_jsonl_lines_split_on_newline_only(self):
        # U+2028 is legal inside a JSON string; str.splitlines() would cut the record in two
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.jsonl")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"company": "A", "title": "x\u2028y"}, ensure_ascii=False) + "\n")
            self.assertEqual(jobdupes.load_postings(path)[0]["title"], "x\u2028y")

    def test_key_mode(self):
        code, out = run("--key", "--company", "Acme Corp", "--title", "SOC Analyst (L2)")
        self.assertEqual((code, out.strip()), (0, "acme-corp_soc-analyst-l2"))


if __name__ == "__main__":
    unittest.main()
