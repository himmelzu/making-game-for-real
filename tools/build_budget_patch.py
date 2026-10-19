#!/usr/bin/env python3
"""Reviewable funding patch for the inspected FM26 league XML.

Only confirmed, pre-existing finance record keys receive initial budgets.
Every league tier receives the same scheduled each-team base payment without
guessing how a competition's team reference maps to a club finance record.
No game runtime, binary format, or FMF compiler is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = "247c644252d2e1312c579bd6633a26788163e5d027d888f193931621d762963b"
BALANCE = 100_000_000
TRANSFER_BUDGET = 50_000_000
REMAINING_WAGE_BUDGET = 1_000_000
ANNUAL_PAYMENT = 100_000_000
PAYMENT_MONTH = "aug"
PAYMENT_DAY = 11
INITIAL_VALUES = {"Ccba": BALANCE, "Ctbu": TRANSFER_BUDGET,
                  "Crmb": REMAINING_WAGE_BUDGET}
# DBIDs in the basic league rules; the third through fifth are parent leagues.
LEAGUES = {2000167898: (1, 20), 2000180088: (2, 20),
           2000302444: (3, 60), 2000326923: (4, 120),
           2000534802: (5, 240), 2000338755: (6, 40)}
EXISTING_ANNUAL = {1: 1_000_000, 2: 500_000, 3: 250_000,
                   4: 100_000, 5: 50_000}
SCHEMA_REFERENCE = ("https://github.com/AlessandroCarcangiu/"
                    "fm-pre-game-massive-editor/blob/"
                    "120ba92fad9dd586bdf9d2d299c88380bfb4c273/settings/club.yaml")


def need(ok, message):
    if not ok:
        raise ValueError(message)


def value(node, key, default=None):
    child = node.find(f"*[@id='{key}']")
    return default if child is None else child.get("value", default)


def property_code(record):
    return int(value(record, "property")).to_bytes(4, "big").decode("ascii")


def inspect_database(root):
    db = root.find("list[@id='db_changes']")
    need(db is not None, "Missing database changes")
    latest, created, deleted = {}, set(), set()
    for index, record in enumerate(db):
        table, uid, code = value(record, "database_table_type"), value(record, "db_unique_id"), property_code(record)
        if table == "55" and code == "ADDR":
            new = record.find("record[@id='new_value']")
            if new is not None and value(new, "database_table_type") == "3":
                created.add(value(new, "db_unique_id"))
        if table == "3":
            latest[uid, code] = (index, record)
            if code == "DELE":
                deleted.add(uid)
    counts = Counter()
    roster = []
    for (uid, code), (_, record) in latest.items():
        if code != "Cdvi" or uid in deleted:
            continue
        new = record.find("record[@id='new_value']")
        if new is None or value(new, "DBID") is None:
            continue
        dbid = int(value(new, "DBID"))
        if dbid in LEAGUES:
            level = LEAGUES[dbid][0]
            counts[level] += 1
            roster.append({"competition_assignment_record_uid": uid,
                           "league_dbid": dbid, "level": level})
    need(counts == Counter({level: count for level, count in LEAGUES.values()}),
         f"Unexpected league roster: {dict(counts)}")
    # These keys come from actual created-club financial edits, never Ttea or
    # Cdvi reference arithmetic. The original includes 39 such celebrity clubs.
    finance_uids = [uid for uid in created if uid not in deleted
                    and (uid, "PLgn") in latest
                    and value(latest[uid, "PLgn"][1], "new_value") == "1"
                    and all((uid, code) in latest for code in INITIAL_VALUES)]
    need(len(finance_uids) == 39, "Unexpected confirmed celebrity finance records")
    return db, latest, sorted(finance_uids, key=int), sorted(roster, key=lambda x: int(x["competition_assignment_record_uid"]))


def funding_rules(root):
    files = root.find("list[@id='files']")
    need(files is not None and len(files) == 39, "Unexpected advanced rule files")
    chosen = {}
    for index, comp in enumerate(files):
        if value(comp, "type") != "division":
            continue
        ref = value(comp, "competition")
        if ref is None:
            continue
        # Existing competition references can be a DBID or a composite UID.
        # Resolve through the basic six records, which preserve both variants.
        level = int(value(comp, "level")) + 1
        if level not in range(1, 7):
            continue
        if level in (3, 4, 5) and value(comp, "file_type") != "parent_comp":
            continue
        need(level not in chosen, f"Duplicate base payment layer for tier {level}")
        chosen[level] = (index, comp)
    need(set(chosen) == set(range(1, 7)), "Missing league payment rules")
    need([chosen[level][0] for level in range(1, 7)] == [1, 2, 3, 8, 16, 30],
         "Unexpected parent / child payment structure")
    return chosen


def patch_tree(root):
    """Mutate the inspected tree; return exact changes and limitations."""
    db, latest, finance_uids, roster = inspect_database(root)
    chosen = funding_rules(root)
    # Validate everything before making the first edit.
    for uid in finance_uids:
        for code in INITIAL_VALUES:
            new = latest[uid, code][1].find("*[@id='new_value']")
            need(new is not None and new.tag == "integer", f"Unexpected current {code} type for {uid}")
            int(new.get("value"))
    for level, (_, comp) in chosen.items():
        need(value(comp, "year_interval") == "1" and value(comp, "base_year") == "2000",
             "Unexpected recurrence")
        payment = comp.find("list[@id='appearance_money']")
        if level == 6:
            need(payment is None, "Sixth tier already has a payment; refusing double funding")
            continue
        need(payment is not None and len(payment) == 1, f"Unexpected annual payment count for tier {level}")
        record = payment[0]
        need(value(record, "cash") == str(EXISTING_ANNUAL[level]), "Unexpected existing base payment")
        need(value(record, "professional_status") == "", "Unexpected professional-status filter")
        need(value(record, "type") in (None, "each_team"), "Unexpected payment distribution")
        date = record.find("record[@id='payment_date']")
        need(date is not None and value(date, "month") == "jul"
             and value(date, "day_of_month") == "1" and value(date, "year") == "2000",
             "Unexpected annual payment date")
    original_template = deepcopy(chosen[2][1].find("list[@id='appearance_money']"))
    finance_changes = []
    for uid in finance_uids:
        name = next((value(latest[uid, code][1], "new_value")
                     for code in ["Csnm", "Cnam"]
                     if (uid, code) in latest and value(latest[uid, code][1], "new_value")), uid)
        row = {"finance_record_uid": uid, "name": name, "fields": {}}
        for code, amount in INITIAL_VALUES.items():
            index, record = latest[uid, code]
            new = record.find("integer[@id='new_value']")
            row["fields"][code] = {"record_index": index, "before": int(new.get("value")), "after": amount}
            new.set("value", str(amount))
        finance_changes.append(row)
    payment_changes = []
    for level, (index, comp) in chosen.items():
        payment = comp.find("list[@id='appearance_money']")
        added = payment is None
        if added:
            payment = deepcopy(original_template)
            comp.append(payment)
        record = payment[0]
        record.find("integer[@id='cash']").set("value", str(ANNUAL_PAYMENT))
        if record.find("*[@id='type']") is None:
            ET.SubElement(record, "string", {"id": "type", "value": "each_team"})
        else:
            record.find("*[@id='type']").set("value", "each_team")
        # A weekday condition plus day_of_month may defer a payment. Reuse the
        # source's explicitly unconstrained weekday representation from tier 2.
        date = record.find("record[@id='payment_date']")
        date.find("integer[@id='day_of_month']").set("value", str(PAYMENT_DAY))
        date.find("string[@id='month']").set("value", PAYMENT_MONTH)
        weekday = date.find("*[@id='day_of_week']")
        if weekday is not None:
            index_weekday = list(date).index(weekday)
            date.remove(weekday)
            date.insert(index_weekday, ET.Element("null", {"id": "day_of_week"}))
        payment_changes.append({"tier": level, "file_index": index,
                                "payment_list_added": added,
                                "each_team_cash_before": EXISTING_ANNUAL.get(level, 0),
                                "each_team_cash_after": ANNUAL_PAYMENT,
                                "month": PAYMENT_MONTH, "day_of_month": PAYMENT_DAY,
                                "year_interval": 1})
    return {"status": "STATICALLY_CHECKED_EDITOR_AND_GAME_UNVERIFIED",
            "league_teams_covered_by_recurring_rule": len(roster),
            "league_team_counts": dict(sorted(Counter(x["level"] for x in roster).items())),
            "initial_finance_clubs": len(finance_changes),
            "initial_finance_values": INITIAL_VALUES,
            "annual_each_team_payment": ANNUAL_PAYMENT,
            "money_unit": "FM Editor raw money units; currency conversion and weekly wage display not independently verified",
            "finance_schema_reference": SCHEMA_REFERENCE,
            "finance_changes": finance_changes,
            "recurring_payment_changes": payment_changes,
            "league_assignment_records": roster,
            "notes": ["Initial finance values are applied only to 39 confirmed created celebrity club record keys.",
                      "All 500 non-deleted league participants are covered by six tier-level each-team payment rules.",
                      "Competition-assignment references are not treated as finance-record UIDs.",
                      "There is exactly one tier-level recurring payment rule, including parent leagues; child rules are untouched.",
                      "Performance prizes, gates, sponsors, debt, reputation, board budget decisions and player preferences still differ.",
                      "No promise of exact displayed wage budgets, permanent parity, or retroactive changes to an existing save.",
                      "First payment timing and later annual recurrence require FM26 Editor and in-game verification."]}


def build(source):
    need(hashlib.sha256(source).hexdigest() == SOURCE_SHA256, "Unexpected input XML")
    root = ET.fromstring(source)
    report = patch_tree(root)
    output = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    report.update(source_sha256=SOURCE_SHA256, output_sha256=hashlib.sha256(output).hexdigest())
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Inspected unified XML or its data-only ZIP")
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if zipfile.is_zipfile(args.source):
        with zipfile.ZipFile(args.source) as archive:
            need(archive.testzip() is None, "Source archive CRC error")
            source = archive.read("FM26_unified.xml")
    else:
        source = args.source.read_bytes()
    output, report = build(source)
    report_path = args.report or args.output.with_suffix(".report.json")
    need(args.source.resolve() != args.output.resolve(), "Cannot overwrite input")
    bodies = {args.output: output,
              report_path: (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}
    for path, body in bodies.items():
        need(not path.exists() or path.read_bytes() == body, f"Different output already exists: {path}")
    for path, body in bodies.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    print(json.dumps({"output": str(args.output), "report": str(report_path),
                      "initial_finance_clubs": report["initial_finance_clubs"],
                      "recurring_participants": report["league_teams_covered_by_recurring_rule"],
                      "sha256": report["output_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
