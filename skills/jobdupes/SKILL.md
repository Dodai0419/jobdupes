---
name: jobdupes
description: Find duplicate and agency-reposted job postings in a list the user (or a job-search agent) collected, before evaluating or applying. Uses ai-job-search's canonical company+title key, a URL check and career-ops' description SimHash. Offline.
---

# jobdupes

Use when the user has a batch of scraped or saved job postings and wants to drop duplicates, before scoring or applying to a batch, or when a job-search state file (`seen_jobs.json`) may hold the same job twice.

## How to use
1. Put the postings in a JSONL file (one object per line) or a JSON array with `company`, `title`, `url`, `description`, `date` (ISO date). Missing fields are fine.
2. From the repo root run: `python jobdupes.py postings.jsonl --json`
   - add `--today YYYY-MM-DD` to fix the 90-day comparison window; `--threshold 0.92` and `--window 90` are the career-ops defaults
   - one key only: `python jobdupes.py --key --company "Acme Corp" --title "SOC Analyst (L2)" --url URL`
   - check a state file: `python jobdupes.py --audit seen_jobs.json` (exit 1 on malformed keys or duplicate URLs)
3. Read the three groups:
   - `same_key`: same company + title. Keep the newest row unless the URLs differ in a meaningful way.
   - `same_url`: the same posting scraped twice. Keep one.
   - `cross_listed`: near-identical description at a different company, usually a recruiting agency re-post. Tell the user which employer is the original and apply there only.
4. Report what you would drop and ask before deleting anything from the user's files.

## Rules
- Row numbers are 0-based positions in the input file.
- Postings with a description under 200 characters get no fingerprint and never appear in `cross_listed`; that is by design, not a miss.
- Exit code: 0 for reports and keys, 2 when the postings file cannot be read; `--audit` exits 1 on violations.
