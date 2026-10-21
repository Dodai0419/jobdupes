#!/usr/bin/env python3
"""jobdupes: find duplicate and cross-listed job postings, offline.

Fuses two job-search agents' dedup cores into one stdlib file:
  make_key / audit        canonical company+title key, from ai-job-search (Mads Lorentzen, MIT)
  fingerprint / cross     SimHash of the description, from career-ops (Santiago Fernandez de Valderrama, MIT)

Usage:
  python jobdupes.py postings.jsonl [--today YYYY-MM-DD] [--threshold 0.92] [--window 90] [--json]
  python jobdupes.py --key --company "Acme Corp" --title "SOC Analyst (L2)" [--url URL]
  python jobdupes.py --audit seen_jobs.json
"""
import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

# ---------------------------------------------------------------- ai-job-search: canonical key
COMPANY_MAX, TITLE_MAX, HASH_LEN = 40, 60, 6
NON_SLUG = re.compile(r"[^a-z0-9]+")
JOB_ID = re.compile(r"(\d{6,})")
CANONICAL = re.compile(r"^[a-z0-9][a-z0-9-]*_[a-z0-9][a-z0-9-]*$")


def sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:HASH_LEN]


def slugify(text):
    """Lowercase ASCII slug; non-Latin scripts legitimately reduce to ''."""
    ascii_only = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return NON_SLUG.sub("-", ascii_only.lower()).strip("-")


def lost_letters(text):
    """True when the ASCII fold dropped non-Latin letters (Latin ones like 'ø' do not count)."""
    return any(ord(c) >= 128 and c.isalpha() and not unicodedata.name(c, "LATIN").startswith("LATIN")
               for c in unicodedata.normalize("NFKD", str(text or "")))


def cap(slug, limit):
    """Cap length; the hash of the full slug keeps truncation deterministic and collision-free."""
    return slug if len(slug) <= limit else "%s-%s" % (slug[:limit].rstrip("-"), sha(slug))


def make_key(company, title, url=""):
    """The canonical dedup key for one posting: '<company-slug>_<title-slug>'."""
    company_slug = cap(slugify(company), COMPANY_MAX)
    if not company_slug or lost_letters(company):
        name = unicodedata.normalize("NFC", str(company or "").strip().casefold())
        company_slug = "unknown-company" if not name else \
            ("%s-%s" % (company_slug, sha(name)) if company_slug else "company-" + sha(name))
    title_slug = cap(slugify(title), TITLE_MAX)
    if not title_slug or lost_letters(title):
        fragment, match = title_slug, JOB_ID.search(url or "")
        if match:  # the portal's numeric id is the stable handle
            title_slug = "%s-%s" % (fragment, match.group(1)) if fragment else match.group(1)
        else:  # the URL is the posting's identity; with no URL only the title is left
            basis = slugify(unicodedata.normalize("NFKD", str(title or url or "")))
            digest = sha(str(url) if url else str(title))
            title_slug = "%s-%s" % (fragment, digest) if fragment else (basis or "untitled-" + digest)
    return "%s_%s" % (company_slug, title_slug)


def is_legacy(key):
    """Old three-part company_title_location keys: harmless, but not what make_key produces."""
    return bool(key) and key.count("_") > 1 and all(re.fullmatch(r"[a-z0-9][a-z0-9-]*", p) for p in key.split("_") if p)


def audit(path):
    """Check a seen_jobs.json state file; exit 1 on malformed keys or duplicate URLs."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        print("cannot read %s: %s" % (path, e), file=sys.stderr)
        return 1
    seen = doc.get("seen", doc) if isinstance(doc, dict) else doc
    if not isinstance(seen, dict):
        print("%s: expected an object of job entries" % path, file=sys.stderr)
        return 1
    for k, v in seen.items():
        if not isinstance(v, dict):
            print("%s: entry is not an object: %s" % (path, k), file=sys.stderr)
            return 1
    for k, v in seen.items():
        if v.get("url") is not None and not isinstance(v["url"], str):
            print("%s: entry url is not a string: %s" % (path, k), file=sys.stderr)
            return 1
    malformed = [k for k in seen if not (k and CANONICAL.match(k)) and not is_legacy(k)]
    by_url = {}
    for k, v in seen.items():
        if (v.get("url") or "").rstrip("/"):
            by_url.setdefault(v["url"].rstrip("/"), []).append(k)
    dupes = {u: ks for u, ks in by_url.items() if len(ks) > 1}
    drift = [k for k, v in seen.items() if k and CANONICAL.match(k)
             and k != make_key(v.get("company", ""), v.get("title", ""), v.get("url", ""))]
    print(json.dumps({"entries": len(seen), "malformed_keys": malformed,
                      "legacy_three_part_keys": [k for k in seen if is_legacy(k)],
                      "duplicate_urls": dupes, "keys_not_matching_current_rule": len(drift)},
                     indent=2, ensure_ascii=False))
    return 1 if (malformed or dupes) else 0


# ---------------------------------------------------------------- career-ops: description SimHash
FINGERPRINT_MIN_TEXT, CROSSLIST_THRESHOLD, CROSSLIST_WINDOW_DAYS = 200, 0.92, 90
JS_WS = "\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"  # JavaScript's \s
TAG, ENTITY, URL = re.compile(r"<[^>]*>"), re.compile(r"&[a-z#0-9]+;"), re.compile("https?://[^%s]+" % JS_WS)
FP_RE = re.compile(r"[0-9a-f]{16}")
BIT = [bytes(v >> (7 - j) & 1 for v in range(256)) for j in range(8)]  # byte value -> is bit j set


class Fold(dict):
    """str.translate table, filled lazily: keep chars whose category starts with one of `cats`."""

    def __init__(self, cats, other):
        super().__init__()
        self.cats, self.other = cats, other

    def __missing__(self, o):
        self[o] = o if unicodedata.category(chr(o))[0] in self.cats else self.other
        return self[o]


JD_FOLD, KEY_FOLD = Fold("LN", " "), Fold("LMN", None)


def normalize_jd(text):
    """Strip tags, entities and URLs, lowercase, collapse everything but letters/digits to single spaces."""
    s = URL.sub(" ", ENTITY.sub(" ", TAG.sub(" ", str("" if text is None else text).lower())))
    return " ".join(t for t in s.translate(JD_FOLD).split(" ") if t)


def fingerprint(text):
    """64-bit SimHash over 3-token shingles as 16 hex chars, or '' when too short to fingerprint."""
    norm = normalize_jd(text)
    if len(norm.encode("utf-16-le")) // 2 < FINGERPRINT_MIN_TEXT:  # JS .length counts UTF-16 units
        return ""
    tokens = norm.split(" ")
    if len(tokens) < 3:
        return ""
    sha1 = hashlib.sha1
    heads = b"".join(sha1(" ".join(tokens[i:i + 3]).encode("utf-8")).digest()[:8] for i in range(len(tokens) - 2))
    h, n = 0, len(heads) // 8
    for pos in range(8):  # bit (pos, j) is set when more than half of the shingle hashes have it set
        column = heads[pos::8]
        for j in range(8):
            if 2 * column.translate(BIT[j]).count(1) > n:
                h |= 1 << (63 - pos * 8 - j)
    return "%016x" % h


def text_key(value):
    """career-ops normalizeTextKey: NFKC, lowercase, drop U+0307, keep letters, marks and digits."""
    return unicodedata.normalize("NFKC", str("" if value is None else value)).lower() \
        .replace("\u0307", "").translate(KEY_FOLD)


def parse_date(s):
    """Milliseconds like JS Date.parse for ISO dates; None when unparseable.

    shortcut: only ISO 8601 is read (JS also guesses formats like 'Oct 1, 2026'); add formats if a source needs them."""
    s = str(s or "").strip()
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def cross_listings(offers, history, today=None, threshold=CROSSLIST_THRESHOLD, window_days=CROSSLIST_WINDOW_DAYS):
    """Offers whose fingerprint is near-identical to a recent history row from a DIFFERENT company, best first.

    Same result and order as career-ops' offers x history scan, but candidates come from a pigeonhole index:
    two hashes within d bits agree exactly on at least one of d+1 bit blocks."""
    now = parse_date(today) if today else time.time() * 1000
    cutoff = now - window_days * 86400000
    max_dist = next((d - 1 for d in range(65) if not 1 - d / 64 >= threshold), 64)
    zero_ok = 0 >= threshold
    recent = []
    for row in history:
        t = parse_date(row.get("date")) if row.get("fingerprint") else None
        if t is not None and t >= cutoff:
            valid = bool(FP_RE.fullmatch(row["fingerprint"]))
            recent.append((row, text_key(row.get("company")), valid, int(row["fingerprint"], 16) if valid else 0))
    k = max_dist + 1
    bounds = [64 * b // k for b in range(k + 1)] if 0 < k <= 16 and not zero_ok else None
    index = {}
    for n, (_, _, valid, rh) in enumerate(recent if bounds else ()):
        for b in range(k) if valid else ():
            index.setdefault((b, rh >> bounds[b] & (1 << bounds[b + 1] - bounds[b]) - 1), []).append(n)
    out = []
    for offer in offers:
        fp = offer.get("fingerprint")
        if not fp:
            continue
        valid, company = bool(FP_RE.fullmatch(fp)), text_key(offer.get("company"))
        if not valid and not zero_ok:
            continue
        h = int(fp, 16) if valid else 0
        if bounds:
            cands = sorted({n for b in range(k) for n in index.get(
                (b, h >> bounds[b] & (1 << bounds[b + 1] - bounds[b]) - 1), ())})
        else:
            cands = range(len(recent))
        for n in cands:
            row, key, row_valid, rh = recent[n]
            if key == company or row.get("url") == offer.get("url"):
                continue  # same company is a re-post, not a cross-listing
            if valid and row_valid:
                dist = bin(h ^ rh).count("1")
                if dist <= max_dist:
                    out.append((offer, row, 1 - dist / 64))
            elif zero_ok:
                out.append((offer, row, 0))
    return sorted(out, key=lambda m: -m[2])


# ---------------------------------------------------------------- report
FIELDS = {"company": ("company", "employer"), "title": ("title", "role", "position"), "url": ("url", "link"),
          "description": ("description", "descriptionPlain", "body"), "date": ("date", "dateStr", "posted")}


def load_postings(path):
    """JSONL or a JSON array of postings; common field aliases are accepted."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    raw = json.loads(text) if text.lstrip().startswith("[") else [json.loads(l) for l in text.split("\n") if l.strip()]
    return [{k: next((p[a] for a in names if p.get(a) is not None), "") for k, names in FIELDS.items()} for p in raw]


def groups(rows, keyfn):
    seen = {}
    for i, r in enumerate(rows):
        k = keyfn(r)
        if k:
            seen.setdefault(k, []).append(i)
    return [(k, ix) for k, ix in seen.items() if len(ix) > 1]


def report(rows, today=None, threshold=CROSSLIST_THRESHOLD, window_days=CROSSLIST_WINDOW_DAYS):
    items = [dict(r, fingerprint=fingerprint(r["description"]), i=i) for i, r in enumerate(rows)]
    pairs, done = [], set()
    for o, h, s in cross_listings(items, items, today, threshold, window_days):
        if (h["i"], o["i"]) not in done:  # rows are both offers and history: fold the mirrored pair
            done.add((o["i"], h["i"]))
            pairs.append({"rows": [o["i"], h["i"]], "score": s})
    return {"postings": len(rows),
            "same_key": [{"key": k, "rows": ix} for k, ix in groups(rows, lambda r: make_key(r["company"], r["title"], r["url"]))],
            "same_url": [{"url": u, "rows": ix} for u, ix in groups(rows, lambda r: str(r["url"]).rstrip("/"))],
            "cross_listed": pairs}


def print_report(rep, rows):
    print("jobdupes - %d postings (offline)\n" % rep["postings"])
    print("same company + title (ai-job-search key): %d groups" % len(rep["same_key"]))
    for g in rep["same_key"]:
        print("  %-60s rows %s" % (g["key"], ", ".join(map(str, g["rows"]))))
    print("\nsame URL: %d groups" % len(rep["same_url"]))
    for g in rep["same_url"]:
        print("  %-60s rows %s" % (g["url"][:60], ", ".join(map(str, g["rows"]))))
    print("\ncross-listed (near-identical description, different company): %d pairs" % len(rep["cross_listed"]))
    for p in rep["cross_listed"]:
        a, b = (rows[i] for i in p["rows"])
        print("  %.2f  row %d %s / %s  <->  row %d %s / %s" % (p["score"], p["rows"][0], a["company"] or "?", a["title"][:30],
                                                         p["rows"][1], b["company"] or "?", b["title"][:30]))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Find duplicate and cross-listed job postings, offline.")
    ap.add_argument("postings", nargs="?", help="JSONL or JSON array of {company, title, url, description, date}")
    ap.add_argument("--today", help="reference date for the cross-listing window (default: now)")
    ap.add_argument("--threshold", type=float, default=CROSSLIST_THRESHOLD)
    ap.add_argument("--window", type=int, default=CROSSLIST_WINDOW_DAYS, help="days of history to compare")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--key", action="store_true", help="print the canonical key for --company/--title/--url")
    ap.add_argument("--company")
    ap.add_argument("--title")
    ap.add_argument("--url", default="")
    ap.add_argument("--audit", metavar="SEEN_JOBS_JSON", help="check a seen_jobs.json state file")
    a = ap.parse_args(argv)
    if a.audit:
        return audit(a.audit)
    if a.key:
        if a.company is None or a.title is None:
            ap.error("--key needs --company and --title")
        print(make_key(a.company, a.title, a.url))
        return 0
    if not a.postings:
        ap.error("give a postings file, --key or --audit")
    try:
        rows = load_postings(a.postings)
    except (OSError, ValueError) as e:
        print("cannot read %s: %s" % (a.postings, e), file=sys.stderr)
        return 2
    rep = report(rows, a.today, a.threshold, a.window)
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print_report(rep, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
