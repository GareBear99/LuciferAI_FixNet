#!/usr/bin/env python3
"""Tests for scripts/daily-pages-publish.py — no network, no secret.

Run: python3 scripts/test-daily-pages-publish.py
The publish reads a saved /admin/grab-outcomes response (the
--outcomes-file shape), so the whole flow is exercised locally
against a temp repo dir: daily file, cumulative status table,
latest pointer, day index, .nojekyll, index.html, and --verify.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "daily_pages_publish", os.path.join(HERE, "daily-pages-publish.py"))
PUBLISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISH)

PASSED = 0


def check(name, fn):
    global PASSED
    fn()
    PASSED += 1
    print("ok - %s" % name)


def fixture_outcomes():
    """A grab-outcomes response: 3 temps, 2 verified (one today,
    one yesterday), 1 pending, 1 not_verified (fake genesis)."""
    return {
        "ok": True,
        "grabDay": "2026-11-16",
        "shards": 1,
        "truncated": False,
        "outcomes": [
            {"tick": 1, "id": "profile-root", "outcome": "verified",
             "day": "2026-11-15", "reason": "genesis_root"},
            {"tick": 2, "id": "profile-fake", "outcome": "not_verified",
             "day": "2026-11-16", "reason": "genesis_not_root"},
            {"tick": 3, "id": "profile-child", "outcome": "verified",
             "day": "2026-11-16", "reason": "chain_holds"},
            {"tick": 4, "id": "profile-waiting", "outcome": "pending",
             "day": "2026-11-16", "reason": "referrer_pending"},
        ],
    }


def day_outcomes(all_data, day):
    return {"outcomes": [e for e in all_data["outcomes"] if e.get("day") == day]}


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_publish_writes_the_daily_channel():
    tmp = tempfile.mkdtemp(prefix="fixnet-pages-")
    try:
        PUBLISH.REPO_ROOT = tmp
        all_data = fixture_outcomes()
        day_data = day_outcomes(all_data, "2026-11-16")
        report = PUBLISH.publish("2026-11-16", day_data, all_data)
        assert report["day_outcomes"] == 3, report
        assert report["verified_today"] == 1, report
        assert report["verified_total"] == 2, report
        assert report["deposit_entries"] == 1, report

        daily = read_json(os.path.join(tmp, "docs", "daily", "2026-11-16.json"))
        assert daily["format"] == "fixnet-daily-v1"
        assert daily["date"] == "2026-11-16"
        # Only the day's outcomes are in the daily file (3 of 4).
        assert [e["id"] for e in daily["status"]] == [
            "profile-fake", "profile-child", "profile-waiting"], daily["status"]
        assert daily["counts"] == {
            "verified": 1, "pending": 1, "not_verified": 1, "expired": 0, "checked": 3}
        assert daily["yield"]["verified_today"] == 1
        assert daily["yield"]["verified_total"] == 2
        # The deposit is only the temps verified TODAY.
        assert daily["deposit"]["entries"] == [
            {"id": "profile-child", "tick": 3, "sp": 1000, "snapshot_access": True}]
        assert daily["deposit"]["sp_per_verified"] == 1000
        assert daily["deposit"]["overflow_queued"] is False
        # No corpus has run in the temp repo: yield corpus numbers are null, not guessed.
        assert daily["yield"]["corpus_entries_today"] is None
        assert daily["yield"]["corpus_total_entries"] is None

        # The cumulative status table holds ALL temps (a temp verified
        # yesterday is not in today's daily file, but is here).
        status = read_json(os.path.join(tmp, "docs", "daily", "status.json"))
        assert status["format"] == "fixnet-status-v1"
        assert status["temps"] == 4
        assert status["verified"] == 2
        assert [e["id"] for e in status["status"]] == [
            "profile-root", "profile-fake", "profile-child", "profile-waiting"]
        assert status["status"][0]["reason"] == "genesis_root"

        latest = read_json(os.path.join(tmp, "docs", "daily", "latest.json"))
        assert latest["date"] == "2026-11-16"
        assert latest["daily"] == "daily/2026-11-16.json"
        index = read_json(os.path.join(tmp, "docs", "daily", "index.json"))
        assert index["days"] == ["2026-11-16"]
        assert index["latest"] == "2026-11-16"
        assert os.path.exists(os.path.join(tmp, "docs", ".nojekyll"))
        html = open(os.path.join(tmp, "docs", "index.html"), encoding="utf-8").read()
        assert "2026-11-16.json" in html and "status.json" in html
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_publish_is_idempotent_and_verify_passes():
    tmp = tempfile.mkdtemp(prefix="fixnet-pages-")
    try:
        PUBLISH.REPO_ROOT = tmp
        all_data = fixture_outcomes()
        day_data = day_outcomes(all_data, "2026-11-16")
        PUBLISH.publish("2026-11-16", day_data, all_data)
        first = read_json(os.path.join(tmp, "docs", "daily", "2026-11-16.json"))
        PUBLISH.publish("2026-11-16", day_data, all_data)
        second = read_json(os.path.join(tmp, "docs", "daily", "2026-11-16.json"))
        # Everything but the generation timestamp is identical on re-run.
        first.pop("generated_at")
        second.pop("generated_at")
        assert first == second, "re-publish changed the daily file"
        ok, errors = PUBLISH.verify_published("2026-11-16", day_data, all_data)
        assert ok, errors
        # Verify catches a tampered daily file.
        daily_path = os.path.join(tmp, "docs", "daily", "2026-11-16.json")
        tampered = read_json(daily_path)
        tampered["status"] = tampered["status"][:1]
        with open(daily_path, "w", encoding="utf-8") as f:
            json.dump(tampered, f)
        ok2, errors2 = PUBLISH.verify_published("2026-11-16", day_data, all_data)
        assert not ok2 and errors2, "verify should fail on a tampered file"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_invalid_outcomes_are_skipped_not_published():
    tmp = tempfile.mkdtemp(prefix="fixnet-pages-")
    try:
        PUBLISH.REPO_ROOT = tmp
        all_data = {"ok": True, "outcomes": [
            {"tick": 1, "id": "profile-ok", "outcome": "verified",
             "day": "2026-11-16", "reason": "chain_holds"},
            {"tick": -1, "id": "profile-bad-tick", "outcome": "verified",
             "day": "2026-11-16", "reason": "chain_holds"},
            {"tick": 2, "id": "", "outcome": "verified",
             "day": "2026-11-16", "reason": "chain_holds"},
            {"tick": 3, "id": "profile-bad-outcome", "outcome": "bogus",
             "day": "2026-11-16", "reason": "chain_holds"},
            {"tick": 4, "id": "profile-ok-2", "outcome": "pending",
             "day": "2026-11-16", "reason": "referrer_pending"},
        ]}
        day_data = day_outcomes(all_data, "2026-11-16")
        report = PUBLISH.publish("2026-11-16", day_data, all_data)
        assert report["day_outcomes"] == 2, report
        assert report["day_outcomes_skipped"] == 3, report
        daily = read_json(os.path.join(tmp, "docs", "daily", "2026-11-16.json"))
        assert [e["id"] for e in daily["status"]] == ["profile-ok", "profile-ok-2"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_corpus_numbers_come_from_the_manifest_when_present():
    tmp = tempfile.mkdtemp(prefix="fixnet-pages-")
    try:
        PUBLISH.REPO_ROOT = tmp
        os.makedirs(os.path.join(tmp, "corpus", "2026-11-16"), exist_ok=True)
        with open(os.path.join(tmp, "corpus", "2026-11-16", "manifest.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"entries": 42, "overflow_queued": True}, f)
        with open(os.path.join(tmp, "refs.json"), "w", encoding="utf-8") as f:
            json.dump({"total_entries": 1042}, f)
        all_data = fixture_outcomes()
        day_data = day_outcomes(all_data, "2026-11-16")
        PUBLISH.publish("2026-11-16", day_data, all_data)
        daily = read_json(os.path.join(tmp, "docs", "daily", "2026-11-16.json"))
        assert daily["yield"]["corpus_entries_today"] == 42
        assert daily["yield"]["corpus_total_entries"] == 1042
        # Overflow queued in the corpus run is recorded in the deposit.
        assert daily["deposit"]["overflow_queued"] is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_day_publishes_an_empty_daily_file():
    tmp = tempfile.mkdtemp(prefix="fixnet-pages-")
    try:
        PUBLISH.REPO_ROOT = tmp
        report = PUBLISH.publish("2026-11-16", {"outcomes": []}, {"outcomes": []})
        assert report["day_outcomes"] == 0
        assert report["verified_today"] == 0
        daily = read_json(os.path.join(tmp, "docs", "daily", "2026-11-16.json"))
        assert daily["status"] == []
        assert daily["deposit"]["entries"] == []
        status = read_json(os.path.join(tmp, "docs", "daily", "status.json"))
        assert status["temps"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("publish writes the daily channel (daily file, status table, latest, index, .nojekyll, html)",
      test_publish_writes_the_daily_channel)
check("publish is idempotent and --verify passes (and catches tampering)",
      test_publish_is_idempotent_and_verify_passes)
check("invalid outcomes are skipped, never published",
      test_invalid_outcomes_are_skipped_not_published)
check("corpus numbers come from the manifest when present",
      test_corpus_numbers_come_from_the_manifest_when_present)
check("an empty day publishes an empty daily file",
      test_empty_day_publishes_an_empty_daily_file)

print("\n%d pages-publish tests passed, 0 failed." % PASSED)
