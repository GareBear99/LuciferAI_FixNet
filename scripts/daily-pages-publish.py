#!/usr/bin/env python3
"""FixNet daily Pages publish.

Publishes one day's grab results (temp verification status + the yield
+ the deposit data) to the FixNet repo's GitHub Pages site, so the
Lucid client can pull them. This is step 3 of the dailies: the 12am
grab verifies temps (step 2), this script publishes the results, and
the client syncs them pull-only (step 4).

Design (Gary, 2026-11-16):
- GitHub Pages is the daily channel. The FixNet repo's Pages site
  publishes the daily data: temp verification status, the yield, the
  deposit data, everything. The client pulls it from Pages. There is
  no separate status API.
- The client sync is PULL-ONLY. The client reads these files and
  applies what it finds (its own temp's status, the deposit). Nothing
  leaves the machine via the sync.
- The deposit is GitHub Pages -> neurons, once a day. The daily file
  carries the deposit entries (each verified temp: 1,000 SP/day +
  snapshot access); the client applies its own entry once a day.
  When the 1B/day cap is hit and overflow queues, the deposit queues
  too (the daily file records overflow_queued from the corpus run).

What it writes (under docs/ — the GitHub Pages source):
- docs/daily/<date>.json — the day's record: status table for the
  day, counts, the yield, the deposit entries.
- docs/daily/latest.json — a pointer to the latest day's file.
- docs/daily/status.json — the cumulative status table: the latest
  outcome per temp, all days, in tick order. This is what the client
  reads to find its own temp's status (a temp verified yesterday is
  not in today's daily file).
- docs/daily/index.json — the list of published days.
- docs/index.html — a minimal human-readable page for the site.
- docs/.nojekyll — so Jekyll never processes the JSON files.

Source of truth: GET /admin/grab-outcomes on the ping server (admin
key). The script never invents a status: every entry in the published
files comes from the outcome log, with the day and the reason the grab
recorded. The corpus numbers (entries today / total) come from the
day's corpus manifest + refs.json when the corpus update has run;
when they have not run yet the yield carries nulls, not guesses.

Idempotency: re-running for a day rewrites the same files with the
same content (the outcome log is append-only by key, latest wins).
A re-publish after a late grab chunk picks up the new outcomes.

Cost: 0 neurons. Pages is a static file host; the publish is data
moving, not AI. The client pull is the daily pull (the pay).

Usage:
    PING_SHARED_SECRET=<secret> python3 scripts/daily-pages-publish.py [--date YYYY-MM-DD]
    python3 scripts/daily-pages-publish.py --outcomes-file <outcomes.json> [--date YYYY-MM-DD]
    python3 scripts/daily-pages-publish.py --verify --date 2026-11-16

The secret comes from the environment (PING_SHARED_SECRET), never from
a file or an argument. --outcomes-file reads a saved
/admin/grab-outcomes response instead of the live server (local
testing; the file shape is the route's { ok, grabDay, outcomes }).
--verify checks the published files for a day against the live
outcomes (or the --outcomes-file) and reports mismatches.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date as _date
from datetime import datetime, timezone

# --- Constants -------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE_URL = "https://lucid-ping-server.admension.workers.dev"
SP_VERIFIED_DAILY = 1000  # sovereignPoints.ts SP_VERIFIED_DAILY — same number
PAGES_DIR = "docs"
DAILY_FORMAT = "fixnet-daily-v1"
STATUS_FORMAT = "fixnet-status-v1"

OUTCOMES = ("verified", "pending", "not_verified", "expired")


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


def fetch_outcomes(base_url, secret, day=None):
    """GET /admin/grab-outcomes (optionally filtered to one grab day)."""
    url = base_url.rstrip("/") + "/admin/grab-outcomes"
    if day:
        url += "?day=" + day
    req = urllib.request.Request(url, headers={
        "x-lucid-ping-key": secret,
        "accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            data = json.load(res)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            "grab-outcomes HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:300])
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError("grab-outcomes unreachable: %s" % e) from e
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError("grab-outcomes bad response: %s" % json.dumps(data)[:300])
    if not isinstance(data.get("outcomes"), list):
        raise RuntimeError("grab-outcomes response has no outcomes list")
    return data


def load_outcomes_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("outcomes"), list):
        raise ValueError("outcomes file must be a /admin/grab-outcomes response ({ ok, outcomes })")
    return data


def validate_outcome(entry):
    """An outcome entry is usable only if it has the fields the client needs."""
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("tick"), int) or entry["tick"] < 0:
        return False
    if not isinstance(entry.get("id"), str) or not entry["id"]:
        return False
    if entry.get("outcome") not in OUTCOMES:
        return False
    return True


def build_daily(day_outcomes, all_outcomes, day, corpus_manifest, refs_index):
    """The day's published record, from the outcome log + the corpus numbers."""
    day_outcomes = sorted(day_outcomes, key=lambda e: e["tick"])
    counts = {o: 0 for o in OUTCOMES}
    for e in day_outcomes:
        counts[e["outcome"]] += 1
    verified_today = [e for e in day_outcomes if e["outcome"] == "verified"]
    verified_total = sum(1 for e in all_outcomes if e["outcome"] == "verified")
    deposit_entries = [
        {"id": e["id"], "tick": e["tick"], "sp": SP_VERIFIED_DAILY, "snapshot_access": True}
        for e in verified_today
    ]
    corpus_today = corpus_manifest.get("entries") if corpus_manifest else None
    corpus_total = refs_index.get("total_entries") if refs_index else None
    overflow_queued = bool(corpus_manifest.get("overflow_queued")) if corpus_manifest else False
    return {
        "format": DAILY_FORMAT,
        "date": day,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": [
            {
                "tick": e["tick"],
                "id": e["id"],
                "outcome": e["outcome"],
                "day": e.get("day"),
                "reason": e.get("reason"),
            }
            for e in day_outcomes
        ],
        "counts": dict(counts, checked=len(day_outcomes)),
        "yield": {
            "verified_today": len(verified_today),
            "verified_total": verified_total,
            "corpus_entries_today": corpus_today,
            "corpus_total_entries": corpus_total,
        },
        "deposit": {
            "sp_per_verified": SP_VERIFIED_DAILY,
            "overflow_queued": overflow_queued,
            "entries": deposit_entries,
        },
    }


def build_status(all_outcomes, day):
    """The cumulative status table: latest outcome per temp, tick order."""
    by_id = {}
    for e in all_outcomes:
        by_id[e["id"]] = e
    entries = sorted(by_id.values(), key=lambda e: e["tick"])
    return {
        "format": STATUS_FORMAT,
        "date": day,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "temps": len(entries),
        "verified": sum(1 for e in entries if e["outcome"] == "verified"),
        "status": [
            {
                "tick": e["tick"],
                "id": e["id"],
                "outcome": e["outcome"],
                "day": e.get("day"),
                "reason": e.get("reason"),
            }
            for e in entries
        ],
    }


def load_json_if_exists(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def render_index_html(days):
    rows = "\n".join(
        '  <li><a href="daily/%s.json">%s</a></li>' % (d, d) for d in days
    )
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>FixNet daily</title></head>
<body>
<h1>FixNet daily</h1>
<p>The daily channel: temp verification status, the yield, and the
deposit data, published once a day after the 12am grab. The Lucid
client pulls these files (pull-only) every 6 hours and on startup.</p>
<p><a href="daily/status.json">Current status (all temps)</a> &middot;
<a href="daily/latest.json">Latest day</a></p>
<ul>
%s
</ul>
</body>
</html>
""" % rows


def publish(day, day_data, all_data):
    """Write the day's Pages files. Returns the list of files written."""
    day_outcomes_raw = day_data.get("outcomes", [])
    all_outcomes_raw = all_data.get("outcomes", [])
    day_outcomes = [e for e in day_outcomes_raw if validate_outcome(e)]
    all_outcomes = [e for e in all_outcomes_raw if validate_outcome(e)]
    skipped_day = len(day_outcomes_raw) - len(day_outcomes)
    skipped_all = len(all_outcomes_raw) - len(all_outcomes)

    corpus_manifest = load_json_if_exists(repo_path("corpus", day, "manifest.json"))
    refs_index = load_json_if_exists(repo_path("refs.json"))

    daily = build_daily(day_outcomes, all_outcomes, day, corpus_manifest, refs_index)
    status = build_status(all_outcomes, day)

    docs = repo_path(PAGES_DIR)
    daily_dir = os.path.join(docs, "daily")
    written = []

    write_json(os.path.join(daily_dir, "%s.json" % day), daily)
    written.append("docs/daily/%s.json" % day)
    write_json(os.path.join(daily_dir, "latest.json"), {
        "format": DAILY_FORMAT,
        "date": day,
        "daily": "daily/%s.json" % day,
        "status": "daily/status.json",
    })
    written.append("docs/daily/latest.json")
    write_json(os.path.join(daily_dir, "status.json"), status)
    written.append("docs/daily/status.json")

    # The day index: every published day, newest first.
    days = set()
    if os.path.isdir(daily_dir):
        for name in os.listdir(daily_dir):
            if name.endswith(".json") and len(name) == 15 and name[:10] >= "2000-01-01":
                days.add(name[:-5])
    days.add(day)
    days = sorted(days, reverse=True)
    write_json(os.path.join(daily_dir, "index.json"), {
        "format": "fixnet-daily-index-v1",
        "days": days,
        "latest": day,
    })
    written.append("docs/daily/index.json")

    with open(os.path.join(docs, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index_html(days))
    written.append("docs/index.html")
    nojekyll = os.path.join(docs, ".nojekyll")
    if not os.path.exists(nojekyll):
        open(nojekyll, "w", encoding="utf-8").close()
    written.append("docs/.nojekyll")

    return {
        "date": day,
        "written": written,
        "day_outcomes": len(day_outcomes),
        "day_outcomes_skipped": skipped_day,
        "all_outcomes": len(all_outcomes),
        "all_outcomes_skipped": skipped_all,
        "verified_today": daily["yield"]["verified_today"],
        "verified_total": daily["yield"]["verified_total"],
        "deposit_entries": len(daily["deposit"]["entries"]),
        "overflow_queued": daily["deposit"]["overflow_queued"],
    }


def verify_published(day, day_data, all_data):
    """Check the published files for a day against the outcome log."""
    daily_path = repo_path(PAGES_DIR, "daily", "%s.json" % day)
    status_path = repo_path(PAGES_DIR, "daily", "status.json")
    errors = []
    if not os.path.exists(daily_path):
        return False, ["missing docs/daily/%s.json" % day]
    if not os.path.exists(status_path):
        return False, ["missing docs/daily/status.json"]
    daily = load_json_if_exists(daily_path)
    status = load_json_if_exists(status_path)
    day_outcomes = sorted(
        [e for e in day_data.get("outcomes", []) if validate_outcome(e)],
        key=lambda e: e["tick"],
    )
    all_outcomes = [e for e in all_data.get("outcomes", []) if validate_outcome(e)]
    if daily.get("date") != day:
        errors.append("daily file date %r != %r" % (daily.get("date"), day))
    published = daily.get("status", [])
    if len(published) != len(day_outcomes):
        errors.append("daily status has %d entries, outcome log has %d for the day"
                      % (len(published), len(day_outcomes)))
    else:
        for p, e in zip(published, day_outcomes):
            if (p.get("tick"), p.get("id"), p.get("outcome")) != (e["tick"], e["id"], e["outcome"]):
                errors.append("daily status mismatch at tick %s: %r vs %r"
                              % (e["tick"], p, e))
                break
    status_ids = {e["id"] for e in status.get("status", [])}
    missing = [e["id"] for e in all_outcomes if e["id"] not in status_ids]
    if missing:
        errors.append("status table is missing %d temps from the outcome log" % len(missing))
    return (len(errors) == 0), errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=_date.today().isoformat(),
                        help="publish day (default: today)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="ping server base URL (default: the live worker)")
    parser.add_argument("--outcomes-file",
                        help="read a saved /admin/grab-outcomes response instead of the live server")
    parser.add_argument("--day-outcomes-file",
                        help="read the day's outcomes from a saved response (with --outcomes-file for the full table)")
    parser.add_argument("--verify", action="store_true",
                        help="verify the published files for --date against the outcomes")
    args = parser.parse_args(argv)

    if args.outcomes_file:
        all_data = load_outcomes_file(args.outcomes_file)
        if args.day_outcomes_file:
            day_data = load_outcomes_file(args.day_outcomes_file)
        else:
            day_data = {"outcomes": [e for e in all_data.get("outcomes", [])
                                     if e.get("day") == args.date]}
    else:
        secret = os.environ.get("PING_SHARED_SECRET")
        if not secret:
            parser.error("PING_SHARED_SECRET is not set (or pass --outcomes-file for local testing)")
        all_data = fetch_outcomes(args.base_url, secret)
        day_data = fetch_outcomes(args.base_url, secret, day=args.date)

    if args.verify:
        ok, errors = verify_published(args.date, day_data, all_data)
        print(json.dumps({"date": args.date, "ok": ok, "errors": errors}, indent=2, sort_keys=True))
        return 0 if ok else 1

    report = publish(args.date, day_data, all_data)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
