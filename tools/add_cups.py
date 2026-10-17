#!/usr/bin/env python3
"""Append three UNVERIFIED custom cups using only inspected FM26 XML shapes.

Input must be the byte-identical cup400 preview. Python 3.10+, standard library.
Never overwrites the input or a different existing output. No FM26 validation.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

SOURCE_SHA256 = "2e70139239ddf7fecaefdc636abb3d7e6711ea5c4d6b9fec6b487f2faf83de8a"
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
NATIONAL = "2000177548"
FA = "2000158691"
TIERS = ["2000167898", "2000180088", "2000302444", "2000326923", "2000534802", "2000338755"]
SPECS = [
    {"name": "여자 포칼 챌린지", "short_name": "포칼 챌린지", "code": "WPC",
     "dbid": 2002000001, "uid_high": 900260001, "template": "2000149343", "tiers": [0, 1, 2],
     "teams": 100, "matches": [36, 32, 16, 8, 4, 2, 1], "extra": [72, 28, 0, 0, 0, 0, 0],
     "reputation": 140,
     "dates": ["2025-09-03", "2025-10-01", "2025-11-05", "2025-12-03", "2026-01-07", "2026-03-04", "2026-04-01"],
     "reference": "독일 포칼의 단판 토너먼트 발상. 통합리그 1~3부 100팀으로 현지화한 창작 대회이며 실제 여자 DFB-Pokal 규정을 재현하지 않음."},
    {"name": "여자 FA 트로피", "short_name": "FA 트로피", "code": "WFT",
     "dbid": 2002000002, "uid_high": 900260002, "template": NATIONAL, "tiers": [4, 5],
     "teams": 280, "matches": [24, 128, 64, 32, 16, 8, 4, 2, 1], "extra": [48, 232, 0, 0, 0, 0, 0, 0, 0],
     "reputation": 80,
     "dates": ["2025-09-03", "2025-10-01", "2025-11-05", "2025-12-03", "2026-01-07", "2026-02-04", "2026-03-04", "2026-04-01", "2026-05-06"],
     "reference": "잉글랜드 FA Trophy의 하부리그 전용 컵 발상. 통합리그 5~6부 280팀에 맞춘 창작 여자 버전이며 실제 참가 자격과 동일하지 않음."},
    {"name": "여자 FA 바스", "short_name": "FA 바스", "code": "WFV",
     "dbid": 2002000003, "uid_high": 900260003, "template": "2000179924", "tiers": [5],
     "teams": 40, "matches": [8, 16, 8, 4, 2, 1], "extra": [16, 24, 0, 0, 0, 0],
     "reputation": 50,
     "dates": ["2025-09-10", "2025-11-12", "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-08"],
     "reference": "잉글랜드 FA Vase의 아마추어 컵 발상. 통합리그 6부 40팀용 창작 여자 버전이며 실제 FA Vase 규정을 재현하지 않음."},
]


def need(ok, message):
    if not ok:
        raise ValueError(message)


def field(node, key):
    result = node.find(f"*[@id='{key}']")
    need(result is not None, f"Missing field {key}")
    return result


def val(node, key):
    return field(node, key).get("value")


def compid(node):
    ref = field(node, "competition")
    return val(ref, "DBID") if ref.tag == "record" else str(int(ref.get("value")) & 0xffffffff)


def find_comp(parent, cid):
    found = [c for c in parent if c.find("*[@id='competition']") is not None and compid(c) == cid]
    need(len(found) == 1, f"Expected one competition {cid}, found {len(found)}")
    return found[0]


def append(parent, node):
    node = copy.deepcopy(node)
    node.tail = parent[-1].tail
    parent[-1].tail = parent.text
    parent.append(node)
    return node


def select(parent, nodes):
    closing = parent[-1].tail
    for c in list(parent):
        parent.remove(c)
    for node in nodes:
        c = copy.deepcopy(node)
        c.tail = parent.text
        parent.append(c)
    need(len(parent) > 0, "Empty selector list")
    parent[-1].tail = closing


def raw(node):
    tail, node.tail = node.tail, None
    try:
        return ET.tostring(node, encoding="utf-8", short_empty_elements=True).replace(b" />", b"/>").replace(b"\n", b"\r\n")
    finally:
        node.tail = tail


def equal(left, right):
    pending = [(left, right)]
    while pending:
        l, r = pending.pop()
        if l.tag != r.tag or l.attrib != r.attrib or (l.text or "").strip() != (r.text or "").strip() or len(l) != len(r):
            return False
        pending.extend(zip(l, r))
    return True


def propcode(text):
    return str(int.from_bytes(text.encode("ascii"), "big"))


def build(source):
    need(hashlib.sha256(source).hexdigest() == SOURCE_SHA256, "Input differs from inspected cup400 preview")
    before = ET.fromstring(source)
    root = copy.deepcopy(before)
    basic = field(field(root, "rule_group"), "cup_comps")
    files = field(root, "files")
    nation = files[0]
    db = field(root, "db_changes")
    need(len(db) == 54699 and len(basic) == 5 and len(files) == 36, "Unexpected source counts")
    need(len(root.findall(".//list[@id='verf']")) == 7 and all(not len(x) for x in root.findall(".//list[@id='verf']")), "Verification caches must already be empty")
    source_values = {e.get("value") for e in before.iter()}
    source_low_ids = {int(v) & 0xffffffff for v in source_values if v and v.isdecimal() and int(v) > 2**32}
    original_db_count = len(db)
    originals = [(field(field(before, "rule_group"), "cup_comps"), basic), (field(before, "files"), files), (field(before, "db_changes"), db)]

    def db_template(table, prop):
        return next(c for c in db if val(c, "database_table_type") == str(table) and val(c, "property") == propcode(prop))

    used_random = {int(val(c, "db_random_id")) for c in db if c.find("*[@id='db_random_id']") is not None}
    next_random = 900260100

    def clone_change(template):
        nonlocal next_random
        result = copy.deepcopy(template)
        while next_random in used_random:
            next_random += 1
        field(result, "db_random_id").set("value", str(next_random))
        used_random.add(next_random)
        next_random += 1
        field(result, "version").set("value", "3733")
        odvl = result.find("*[@id='odvl']")
        if odvl is not None:
            result.remove(odvl)  # ADDR/COPY/newly-created field records do not need an old-value claim.
        return result

    all_logs = []
    for spec in SPECS:
        uid = str((spec["uid_high"] << 32) | spec["dbid"])
        cid = str(spec["dbid"])
        need(cid not in source_values and uid not in source_values and spec["dbid"] not in source_low_ids, "New ID already referenced in export")
        template_basic = find_comp(basic, spec["template"])
        template_advanced = find_comp(files, spec["template"])
        b = copy.deepcopy(template_basic)
        a = copy.deepcopy(template_advanced)
        for node in [b, a]:
            ref = field(node, "competition")
            ref.tag = "large"  # Existing added semi-pro league uses full UID in this exact position.
            ref.set("value", uid)
            field(node, "type").set("value", "cup")
        field(b, "number_teams").set("value", str(spec["teams"]))
        stage = field(a, "stages")[0]
        maximum = stage.find("*[@id='maximum_number_teams']")
        if maximum is not None:
            maximum.set("value", str(spec["teams"]))
        fixed = stage.find("*[@id='number_teams']")
        if fixed is not None:
            fixed.set("value", str(spec["teams"]))
        # The native 40-team cup starts a year late and has a competition-specific
        # named stage. Other original cups omit both; follow their generic form.
        for parent, key in [(a, "year_offset_from_game_start_year"), (stage, "stage_name")]:
            existing = parent.find(f"*[@id='{key}']")
            if existing is not None:
                index = list(parent).index(existing)
                if index > 0:
                    parent[index - 1].tail = existing.tail
                parent.remove(existing)

        fa_basic = find_comp(basic, FA)
        fa_stage = field(find_comp(files, FA), "stages")[0]
        wanted = [TIERS[i] for i in spec["tiers"]]
        select(field(b, "teams"), [e for e in field(fa_basic, "teams") if compid(e) in wanted])
        select(field(stage, "teams"), [e for e in field(fa_stage, "teams") if compid(e) in wanted])
        for selector in field(stage, "teams"):
            field(selector, "seeding").set("value", str(wanted.index(compid(selector))))
        need(sum(int(val(e, "number_teams")) for e in field(b, "teams")) == spec["teams"], "Selector total mismatch")

        brs = field(b, "rounds")
        ars = field(stage, "rounds")
        fixtures = field(a, "fixture_dates")
        need(len(brs) == len(ars) == len(fixtures) == len(spec["dates"]), "Round template length mismatch")
        progression, dates, previous_winners = [], [], 0
        for i, (br, ar, fixture, iso, matches, extra) in enumerate(zip(brs, ars, fixtures, spec["dates"], spec["matches"], spec["extra"])):
            date = dt.date.fromisoformat(iso)
            need(date.weekday() == 2, "Proposed basic date is not Wednesday")
            # The original uses separate 2000/01 advanced and 2025/26 basic
            # calendars. Move three days to retain the same weekday in both.
            advanced_date = dt.date(date.year - 25, date.month, date.day) + dt.timedelta(days=3)
            need(advanced_date.weekday() == 2, "Advanced template date is not Wednesday")
            for rnd in [br, ar]:
                field(rnd, "number_matches").set("value", str(matches))
                field(rnd, "number_extra_teams").set("value", str(extra))
                count = rnd.find("*[@id='number_teams']")
                if count is not None:
                    count.set("value", str(matches * 2))
            need(previous_winners + extra == matches * 2, "Invalid round progression")
            previous_winners = matches
            need(val(ar, "number_legs") == "1" and val(ar, "number_replays") == "0", "Expected single leg, no replay")
            bd = field(br, "date")
            for key, num in [("year", date.year), ("month", date.month), ("day", date.day)]:
                bd.set(key, str(num))
            for key, num in [("year", advanced_date.year), ("month", MONTHS[advanced_date.month - 1]), ("day_of_month", advanced_date.day), ("day_of_week", "wed")]:
                field(fixture, key).set("value", str(num))
            # 100/100 is the source's mixed-tier marker. Vase has one tier only.
            level = "5" if len(wanted) == 1 else "100"
            for key in ["highest_division_level", "lowest_division_level"]:
                field(fixture, key).set("value", level)
            if i:
                need(val(field(ar, "draw_date"), "previous_round_name") == val(ars[i - 1], "stage_name"), "Broken draw linkage")
            need(val(field(ar, "date"), "main_stage_name") == val(fixture, "main_stage_name"), "Fixture/stage reference differs")
            progression.append({"round": i + 1, "teams": matches * 2, "new_entrants": extra, "matches": matches})
            dates.append({"basic": date.isoformat(), "advanced": advanced_date.isoformat()})
        need(sum(spec["extra"]) == spec["teams"] and sum(spec["matches"]) == spec["teams"] - 1 and previous_winners == 1, "Invalid cup arithmetic")
        # The 40-team native cup has elite-level rewards. Scale copied cash by
        # 1/50 for this amateur cup; monetary key shapes remain identical.
        money_changes = []
        if spec["code"] == "WFV":
            for cash in a.findall(".//integer[@id='cash']"):
                old = int(cash.get("value"))
                cash.set("value", str(old // 50))
                money_changes.append({"before": old, "after": old // 50})
        append(basic, b)
        append(files, a)
        registry = append(field(nation, "competitions"), field(nation, "competitions")[-1])
        field(registry, "competition").set("value", uid)
        fixture_rule = append(field(nation, "fixture_rules"), next(c for c in field(nation, "fixture_rules") if compid(c) == spec["template"]))
        field(fixture_rule, "competition").tag = "large"
        field(fixture_rule, "competition").set("value", uid)

        # ADDR creates the destination; COPY's outer ID is the source and its
        # new_value ID is the destination, matching the inspected league copy.
        addr = clone_change(next(c for c in db if val(c, "database_table_type") == "55" and val(c, "property") == propcode("ADDR") and field(c, "new_value").tag == "record" and val(field(c, "new_value"), "database_table_type") == "25"))
        field(field(addr, "new_value"), "db_unique_id").set("value", uid)
        append(db, addr)
        native_ids = {val(c, "db_unique_id") for c in db if val(c, "database_table_type") == "25" and (int(val(c, "db_unique_id")) & 0xffffffff) == int(spec["template"])}
        need(len(native_ids) == 1, "Native cup full UID is ambiguous")
        source_uid = native_ids.pop()
        copy_change = clone_change(db_template(25, "COPY"))
        field(copy_change, "db_unique_id").set("value", source_uid)
        field(field(copy_change, "new_value"), "db_unique_id").set("value", uid)
        append(db, copy_change)
        for prop, new in [("cnam", spec["name"]), ("csnm", spec["short_name"]), ("c3ln", spec["code"]), ("cnti", None), ("PLgn", "1"), ("cext", "false"), ("crep", str(spec["reputation"]))]:
            change = clone_change(db_template(25, prop))
            field(change, "db_unique_id").set("value", uid)
            if prop != "cnti":
                field(change, "new_value").set("value", new)
            else:
                need(val(field(change, "new_value"), "DBID") == "796", "Expected host nation 796")
            append(db, change)
        all_logs.append({**spec, "uid": uid, "source_copy_uid": source_uid, "rounds": progression,
                         "calendar": dates, "amateur_cash_scale": money_changes,
                         "field_policy": "dbty inherited from the native cup via COPY; numeric cup-type enum is not invented"})

    need(len(db) == original_db_count + 27, "Expected 27 appended DB changes")
    need(all(equal(l, r) for l, r in zip(field(before, "db_changes"), list(db)[:original_db_count])), "Original DB changes altered")
    need(all(equal(l, r) for l, r in zip(field(field(before, "rule_group"), "cup_comps"), list(basic)[:5])), "An existing cup basic rule changed")
    need(all(equal(l, r) for l, r in zip(list(field(before, "files"))[1:], list(files)[1:36])), "An existing advanced competition changed")
    for log in all_logs:
        uid = log["uid"]
        for parent in [basic, files, field(nation, "competitions"), field(nation, "fixture_rules")]:
            need(sum(1 for c in parent if c.find(f"*[@id='competition'][@value='{uid}']") is not None) == 1, "Missing or duplicated rule/registry reference")
        need(sum(1 for c in db if val(c, "database_table_type") == "55" and val(c, "property") == propcode("ADDR") and val(field(c, "new_value"), "db_unique_id") == uid) == 1, "Missing ADDR")
        need(sum(1 for c in db if val(c, "database_table_type") == "25" and val(c, "property") == propcode("COPY") and val(field(c, "new_value"), "db_unique_id") == uid) == 1, "Missing COPY")

    # Direct-date collision checks consider tier eligibility. Pokal and Trophy
    # deliberately share dates because their teams never overlap.
    conflicts = []
    for i, a in enumerate(all_logs):
        for b in all_logs[:i]:
            if set(a["tiers"]) & set(b["tiers"]):
                conflicts += [(a["name"], b["name"], d) for d in set(a["dates"]) & set(b["dates"])]
    need(not conflicts, "New cups share a date for eligible teams")
    national_dates = {tuple(val(f, k) for k in ["year", "month", "day_of_month"]) for f in field(find_comp(files, NATIONAL), "fixture_dates")}
    for log in all_logs:
        for d in log["calendar"]:
            x = dt.date.fromisoformat(d["advanced"])
            need((str(x.year), MONTHS[x.month - 1], str(x.day)) not in national_dates, "New cup overlaps national cup template date")

    output, substitutions = source, []
    for old, new in originals:
        old_bytes, new_bytes = raw(old), raw(new)
        need(output.count(old_bytes) == 1, "Source section is not uniquely anchored")
        output = output.replace(old_bytes, new_bytes, 1)
        substitutions.append((old_bytes, new_bytes))
    written = ET.fromstring(output)
    need(equal(root, written), "Written XML differs from intended tree")
    reversed_output = output
    for old_bytes, new_bytes in reversed(substitutions):
        need(reversed_output.count(new_bytes) == 1, "Output section is not uniquely reversible")
        reversed_output = reversed_output.replace(new_bytes, old_bytes, 1)
    need(reversed_output == source, "Bytes outside declared sections changed")
    # Restore three edited lists and compare the whole tree independently.
    for old, new in originals:
        parent = field(root, "rule_group") if old.get("id") == "cup_comps" else root
        pos = list(parent).index(new)
        parent.remove(new)
        parent.insert(pos, copy.deepcopy(old))
    need(equal(root, before), "Change scope exceeds declared lists")
    return output, {"status": "EXPERIMENTAL_UNVERIFIED_NEW_COMPETITIONS", "source_sha256": SOURCE_SHA256,
                    "output_sha256": hashlib.sha256(output).hexdigest(), "cups": all_logs,
                    "static_validation": {"xml_reparse": "passed", "existing_db_changes_preserved": original_db_count,
                        "new_db_changes": 27, "existing_basic_and_advanced_cups_preserved": True,
                        "original_xml_bytes_outside_three_declared_sections": "unchanged",
                        "basic_advanced_arithmetic": "passed", "draw_and_fixture_links": "passed",
                        "new_rule_registry_ADDR_COPY_references": "passed", "UID_DBID_collisions_in_export": "none",
                        "direct_new_cup_or_national_cup_dates_for_same_eligible_tier": "none",
                        "verification_caches": "7 empty; no editor validation"},
                    "runtime_checks_required": [
                        "Load in FM26 Editor: verify all 3 new competition objects, host Spain, women, active, cup type and correct unique IDs.",
                        "COPY and ADDR execution order and full UID resolution require the real editor; static checks cannot prove acceptance.",
                        "Check copied DB history, continental qualification and hidden source-competition properties; remove unwanted copied history/links in Editor if present.",
                        "Verify rules for all six division levels, then simulate at least two seasons and inspect new cup entrant counts and winners.",
                        "The two-calendar three-day relationship follows the source. Recurrence after the first season, league fixtures, international windows and actual rescheduling remain untested.",
                        "Pokal is 100 teams because selecting only 24 of the third tier would require an unverified advanced-selector option.",
                        "FA Trophy 280-team first round has 48 playing and 232 later entrants; FA Vase 40-team first round has 16 playing and 24 later entrants.",
                        "At tier 6 the two new cups can add up to 15 fixtures before existing competitions. Inspect rest, injuries and fixture backlog during simulation.",
                        "These are domestic adaptations of real cup ideas, not authentic DFB/FA rules, Champions League/World Cup activation or national-team competition rules.",
                        "No player aging, nationality, celebrity youth naming or face mapping changes are included in this file.",
                    ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("women-celebrity-three-new-cups-experimental.xml"))
    parser.add_argument("--change-log", type=Path, default=Path(__file__).with_name("new-cups-change-log.json"))
    args = parser.parse_args()
    need(len({args.source.resolve(), args.output.resolve(), args.change_log.resolve()}) == 3, "Input and output paths must differ")
    source = args.source.read_bytes()
    output, log = build(source)
    need(args.source.read_bytes() == source, "Input changed during processing")
    contents = [(args.output, output), (args.change_log, (json.dumps(log, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))]
    for path, data in contents:
        need(not path.exists() or path.read_bytes() == data, f"Refusing to overwrite different existing file {path}")
    for path, data in contents:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as f:
                f.write(data)
    print(json.dumps({"status": log["status"], "output": str(args.output), "cups": len(log["cups"]), "new_db_changes": 27, "sha256": log["output_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
