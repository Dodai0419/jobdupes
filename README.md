<div align="center">

# jobdupes

**Find duplicate and agency-reposted job postings before your agent applies twice. One stdlib file, offline.**

career-ops and ai-job-search, the two most-starred job-search agents for Claude Code, each solve half of dedup. jobdupes runs both halves in one pass.

Works with **Claude Code** and **Codex**.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg) ![Status: prototype](https://img.shields.io/badge/status-prototype-orange.svg)

</div>

## Quick start
```bash
git clone https://github.com/Dodai0419/jobdupes && cd jobdupes
python jobdupes.py examples/postings.jsonl --today 2026-10-20
```
```
same company + title (ai-job-search key): 1 groups
  acme-corp_senior-backend-engineer-payments                   rows 0, 1, 3
same URL: 1 groups
  https://boards.example.io/acme/jobs/4810021                  rows 0, 1
cross-listed (near-identical description, different company): 2 pairs
  0.97  row 0 Acme Corp / Senior Backend Engineer, Payme  <->  row 2 Hays / Backend Engineer (Payments) -
```
Input is JSONL or a JSON array of `{company, title, url, description, date}` (aliases like `role`, `link`, `descriptionPlain` work). Add `--json` for agents. `--key --company X --title Y` prints one canonical key; `--audit seen_jobs.json` checks an ai-job-search state file.

## The problem
- A job scraped twice, once with a trailing slash or a truncated title, gets stored twice and evaluated twice.
- Recruiting agencies re-post the same job under their own name with the employer removed, so company + title and URL checks both miss it.
- ai-job-search catches the first (canonical keys, no agency check); career-ops catches the second (description SimHash) but needs Node and its 768-line tracker parser just to compare two postings.

## The solution
| Idea | Fixes |
|------|-------|
| **ai-job-search's canonical key** | same company + title under any spelling, truncation or Cyrillic title; path-safe for archive folders |
| **career-ops' description SimHash** | agency re-posts: near-identical body, different company, within 90 days |
| **URL check with trailing-slash fold** | the same posting scraped twice |
| **Same answers as upstream** | keys, fingerprints and cross-listing pairs are byte-identical to both tools (tested) |
| **One stdlib Python file** | no Node, no npm install, no network |

## Use with Claude Code
```bash
/plugin marketplace add Dodai0419/jobdupes
/plugin install jobdupes@jobdupes
```

## Use with Codex (ChatGPT)
```bash
git clone https://github.com/Dodai0419/jobdupes && cp -r jobdupes/skills/* ~/.codex/skills/
```
Or copy `AGENTS.md` into your project root.

## Benchmarks
Windows 11, Python 3.11, Node 24, fresh process per run. Upstream = `node` running career-ops' `fingerprintText` + `findCrossListings` over all rows, then `python` running ai-job-search's `make_key` over all rows. jobdupes = `python jobdupes.py FILE --json` (all three checks).

| | original (both tools) | jobdupes |
|-|-|-|
| wall time, 377 postings (`tests/fixtures/postings.jsonl`, median of 9) | 866 ms | 559 ms |
| wall time, 2,484 postings (generated, median of 7) | 2,104 ms | 1,585 ms |
| lines of code | 255 (`job_key.py`) + 262 (`fingerprint-core.mjs`) + 768 (`tracker-parse.mjs`, imported) | 315 |
| runtimes | Python + Node | Python |

Same answers: on the 377-posting fixture and on 1,600 random Unicode-heavy postings (Cyrillic, CJK, Turkish dotted I, astral letters, HTML, entities, U+2028), every key equals ai-job-search's `make_key`, every fingerprint equals career-ops' `fingerprintText`, and the cross-listing pairs, scores and order equal `findCrossListings`. Cross-listing search uses a pigeonhole index (two 64-bit hashes within 5 bits share one of 6 bit blocks) instead of comparing every pair; the output is unchanged. `--audit` output equals `job_key.py --audit`.

## Inspired by
- [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search): `tools/job_key.py`, the canonical company + title key and the state-file audit.
- [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops): `fingerprint-core.mjs` and `normalizeTextKey`, the description SimHash and cross-listing rules.

## Roadmap
- [ ] Read career-ops scan-history TSV directly as `--history`.
- [ ] Re-post detection inside one company (career-ops `detect-reposts.mjs`).
- [ ] More date formats than ISO 8601.

## License
MIT
