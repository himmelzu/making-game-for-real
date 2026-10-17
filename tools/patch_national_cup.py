#!/usr/bin/env python3
"""Create an unverified FM26 cup preview without modifying the input XML.

Python 3.10+; standard library only. This does not run FM26 or its editor.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


SOURCE_SHA256 = "8de7af87d9dfcde8ab6bc03ee5ced64ca388bd1ab85bd0440435a41adf9ed7d2"
CUP_ID = "2000177548"
FA_CUP_ID = "2000158691"
TIER6_ID = "2000338755"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
ADVANCED_DATES = [
    (2000, 8, 30), (2000, 9, 27), (2000, 10, 25),
    (2000, 11, 29), (2000, 12, 20), (2001, 1, 24),
    (2001, 2, 21), (2001, 3, 21), (2001, 4, 25),
]
BASIC_DATES = [
    (2025, 8, 27), (2025, 9, 24), (2025, 10, 22),
    (2025, 11, 26), (2025, 12, 17), (2026, 1, 21),
    (2026, 2, 18), (2026, 3, 18), (2026, 4, 22),
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def child(node: ET.Element, field: str) -> ET.Element:
    result = node.find(f"*[@id='{field}']")
    if result is None:
        raise ValueError(f"Missing field: {field}")
    return result


def value(node: ET.Element, field: str) -> str | None:
    return child(node, field).get("value")


def find_comp(nodes: ET.Element, competition: str) -> ET.Element:
    found = [node for node in nodes if node.find(f"*[@id='competition'][@value='{competition}']") is not None]
    require(len(found) == 1, f"Expected one competition {competition}, found {len(found)}")
    return found[0]


def as_bytes(node: ET.Element) -> bytes:
    # The original editor export uses CRLF and compact empty elements.
    saved_tail = node.tail
    node.tail = None
    try:
        return ET.tostring(node, encoding="utf-8", short_empty_elements=True).replace(b" />", b"/>").replace(b"\n", b"\r\n")
    finally:
        node.tail = saved_tail


def semantic(node: ET.Element):
    return (node.tag, dict(node.attrib), (node.text or "").strip(), [semantic(c) for c in node])


def semantically_equal(left: ET.Element, right: ET.Element) -> bool:
    # Avoid materializing two additional copies of a 23 MB XML tree.
    pending = [(left, right)]
    while pending:
        before, after = pending.pop()
        if before.tag != after.tag or before.attrib != after.attrib or (before.text or "").strip() != (after.text or "").strip() or len(before) != len(after):
            return False
        pending.extend(zip(before, after))
    return True


def differences(before: ET.Element, after: ET.Element, path: str = "/record") -> list[dict]:
    changes = []
    if before.tag != after.tag or before.attrib != after.attrib or (before.text or "").strip() != (after.text or "").strip():
        changes.append({"path": path, "before": {"tag": before.tag, **before.attrib}, "after": {"tag": after.tag, **after.attrib}})
    if len(before) != len(after):
        changes.append({"path": path, "kind": "children_count", "before": len(before), "after": len(after)})
        return changes
    counts = {}
    for left, right in zip(before, after):
        label = f"{left.tag}[@id='{left.get('id')}']" if left.get("id") else left.tag
        counts[label] = counts.get(label, 0) + 1
        changes += differences(left, right, f"{path}/{label}[{counts[label]}]")
    return changes


def write_once_or_identical(path: Path, data: bytes) -> None:
    if path.exists():
        require(path.is_file() and path.read_bytes() == data, f"Refusing to overwrite a different existing file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def build(source: bytes) -> tuple[bytes, dict]:
    require(hashlib.sha256(source).hexdigest() == SOURCE_SHA256, "Input differs from the inspected Ver3 XML; no output produced")
    require(source.count(b"\r\n") == source.count(b"\n"), "Expected the original CRLF export")
    original = ET.fromstring(source)
    draft = copy.deepcopy(original)
    basic_list = child(child(draft, "rule_group"), "cup_comps")
    advanced_list = child(draft, "files")
    basic = find_comp(basic_list, CUP_ID)
    advanced = find_comp(advanced_list, CUP_ID)
    fa_basic = find_comp(basic_list, FA_CUP_ID)
    fa_advanced = find_comp(advanced_list, FA_CUP_ID)
    stage = child(advanced, "stages")[0]
    basic_rounds = child(basic, "rounds")
    advanced_rounds = child(stage, "rounds")
    fixtures = child(advanced, "fixture_dates")
    require(len(basic_rounds) == len(advanced_rounds) == len(fixtures) == 9, "Expected the original nine-round cup")
    require(value(basic, "number_teams") == value(stage, "maximum_number_teams") == "360", "Expected 360-team original cup")
    original_basic = copy.deepcopy(basic)
    original_advanced = copy.deepcopy(advanced)
    original_nation = copy.deepcopy(advanced_list[0])

    edits = []

    def change(node: ET.Element, attribute: str, expected: str, new: str, purpose: str) -> None:
        require(node.get(attribute) == expected, f"Unexpected original value for {purpose}: {node.attrib}")
        node.set(attribute, new)
        edits.append({"purpose": purpose, "attribute": attribute, "before": expected, "after": new})

    change(child(basic, "number_teams"), "value", "360", "400", "basic total entrants")
    change(child(stage, "maximum_number_teams"), "value", "360", "400", "advanced maximum entrants")

    # Reuse the original FA cup's actual sixth-tier selector shapes.
    basic_teams = child(basic, "teams")
    advanced_teams = child(stage, "teams")
    old_basic_selectors = [semantic(e) for e in basic_teams]
    old_advanced_selectors = [semantic(e) for e in advanced_teams]
    sixth_basic = copy.deepcopy(next(e for e in child(fa_basic, "teams") if e.find(f"unsigned[@id='competition'][@value='{TIER6_ID}']") is not None))
    sixth_advanced = copy.deepcopy(next(e for e in child(child(fa_advanced, "stages")[0], "teams") if e.find(f"unsigned[@id='competition'][@value='{TIER6_ID}']") is not None))
    require(value(sixth_basic, "number_teams") == "40", "Expected 40 sixth-tier teams")
    require(value(sixth_advanced, "seeding") == "5", "Expected FA cup sixth-tier seeding")
    child(sixth_advanced, "seeding").set("value", "2")
    sixth_basic.tail = basic_teams[-1].tail
    basic_teams[-1].tail = basic_teams.text
    basic_teams.append(sixth_basic)
    sixth_advanced.tail = advanced_teams.text
    advanced_teams.insert(0, sixth_advanced)
    require([semantic(e) for e in basic_teams[:-1]] == old_basic_selectors, "Existing basic selectors changed")
    require([semantic(e) for e in advanced_teams[1:]] == old_advanced_selectors, "Existing advanced selectors changed")
    edits.append({"purpose": "add sixth-tier selectors", "competition": TIER6_ID, "teams": 40, "advanced_seeding": 2, "source": "same export's FA cup selectors"})

    for rounds, label in [(basic_rounds, "basic"), (advanced_rounds, "advanced")]:
        change(child(rounds[0], "number_matches"), "value", "104", "144", f"{label} round 1 matches")
        change(child(rounds[0], "number_extra_teams"), "value", "208", "288", f"{label} round 1 new entrants")
        change(child(rounds[1], "number_extra_teams"), "value", "152", "112", f"{label} round 2 new entrants")
    change(child(advanced_rounds[0], "number_teams"), "value", "208", "288", "advanced round 1 teams")

    date_changes = []
    for index, (basic_date, advanced_date) in enumerate(zip(BASIC_DATES, ADVANCED_DATES)):
        require(dt.date(*basic_date).weekday() == dt.date(*advanced_date).weekday() == 2, "All proposed dates must be Wednesdays")
        basic_node = child(basic_rounds[index], "date")
        old_basic_date = dict(basic_node.attrib)
        for attr, new in zip(["year", "month", "day"], basic_date):
            basic_node.set(attr, str(new))
        fixture = fixtures[index]
        old_advanced_date = {k: value(fixture, k) for k in ["year", "month", "day_of_month", "day_of_week"]}
        for field, new in [("year", str(advanced_date[0])), ("month", MONTHS[advanced_date[1] - 1]), ("day_of_month", str(advanced_date[2])), ("day_of_week", "wed")]:
            child(fixture, field).set("value", new)
        date_changes.append({"round": index + 1, "basic_before": old_basic_date, "basic_after": dict(basic_node.attrib), "advanced_before": old_advanced_date, "advanced_after": {k: value(fixture, k) for k in old_advanced_date}})

    # Existing subsequent mixed rounds use the same pair. Its precise engine
    # semantics must still be checked in FM26; entrant selection remains explicit.
    for field in ["highest_division_level", "lowest_division_level"]:
        change(child(fixtures[0], field), "value", "4", "100", "first fixture mixed-tier marker: " + field)

    verification = child(draft, "verf")
    original_verification = copy.deepcopy(verification)
    division_levels = child(advanced_list[0], "division_levels")
    require(len(division_levels) == 6, "Expected six original division verification records")
    caches = [("root", verification, 1)] + [(f"division_levels[{i}]", child(division, "verf"), 2) for i, division in enumerate(division_levels)]
    require(len(draft.findall(".//list[@id='verf']")) == len(caches) == 7, "Unexpected verification cache locations")
    for scope, cache, count in caches:
        prior_versions = [c.get("value") for c in cache]
        require(prior_versions == ["26.2.0"] * count, f"Unexpected original verification versions: {scope}")
        saved_tail = cache.tail
        cache.clear()
        cache.set("id", "verf")
        cache.tail = saved_tail
        edits.append({"purpose": "invalidate version verification cache", "scope": scope, "before": prior_versions, "after": [], "field": "verf", "note": "No editor verification has been performed"})
    nation_differences = differences(original_nation, advanced_list[0], "/nation")
    require(len(nation_differences) == 6 and all(c.get("kind") == "children_count" and c["path"].endswith("list[@id='verf'][1]") and c["before"] == 2 and c["after"] == 0 for c in nation_differences), "Nation changes extend beyond division verification caches")
    require(all(len(cache) == 0 for _, cache, _ in caches), "A verification version list was retained")

    # Verify independent basic/advanced arithmetic, input sources and references.
    require(sum(int(value(e, "number_teams")) for e in basic_teams) == 400, "Basic entrant total is not 400")
    require(len(advanced_teams) == 3, "Expected three advanced tier selectors")
    entrant_ids = []
    for e in advanced_teams:
        comp = child(e, "competition")
        entrant_ids.append(comp.get("value") or value(comp, "DBID"))
    require(entrant_ids == [TIER6_ID, "2000534802", "2000326923"], "Unexpected advanced entrant competitions")
    require([value(e, "seeding") for e in advanced_teams] == ["2", "1", "0"], "Unexpected advanced seed order")
    progression = []
    previous_winners = 0
    total_new = total_matches = 0
    for index, (br, ar) in enumerate(zip(basic_rounds, advanced_rounds)):
        matches = int(value(ar, "number_matches"))
        new_teams = int(value(ar, "number_extra_teams"))
        teams = int(value(ar, "number_teams"))
        require(int(value(br, "number_matches")) == matches and int(value(br, "number_extra_teams")) == new_teams, "Basic/advanced round arithmetic differs")
        require(previous_winners + new_teams == teams == matches * 2, f"Invalid round {index + 1} progression")
        require(value(ar, "number_legs") == "1" and value(ar, "number_replays") == "0", "Unexpected legs/replays")
        progression.append({"round": index + 1, "teams": teams, "new_entrants": new_teams, "matches": matches, "winners": matches})
        previous_winners = matches
        total_new += new_teams
        total_matches += matches
    require(total_new == 400 and total_matches == 399 and previous_winners == 1, "Invalid complete cup arithmetic")

    # Round labels, previous-round draw references, match rules and prize money
    # are unchanged: date/first two round counts are the only round differences.
    for old, new in zip(child(child(original_advanced, "stages")[0], "rounds"), advanced_rounds):
        for field in ["stage_name", "draw_date", "date", "subs_rules", "number_legs", "number_replays", "stadium_rules", "winner_prize_money", "loser_prize_money", "appearance_money"]:
            left = old.find(f"*[@id='{field}']")
            right = new.find(f"*[@id='{field}']")
            require((left is None and right is None) or (left is not None and right is not None and semantic(left) == semantic(right)), f"Unexpected advanced round change: {field}")
    for fixture in child(fa_advanced, "fixture_dates"):
        require(value(fixture, "day_of_week") == "sat", "Expected the original FA cup Saturday dates")
    require(all(value(f, "day_of_week") == "wed" for f in fixtures), "National cup dates are not all Wednesday")

    # Replace the nation section last so reverse substitution restores its six
    # empty cache lists before matching the identical empty root cache list.
    replacements = [(original_basic, basic), (original_advanced, advanced), (original_verification, verification), (original_nation, advanced_list[0])]
    output = source
    replacement_log = []
    for before, after in replacements:
        old_chunk, new_chunk = as_bytes(before), as_bytes(after)
        require(output.count(old_chunk) == 1, "Surgical replacement is not uniquely anchored in the source")
        output = output.replace(old_chunk, new_chunk, 1)
        section = before.get("id") or ("national_cup_basic" if before is original_basic else "national_cup_advanced" if before is original_advanced else "nation_division_verification_caches_only")
        replacement_log.append({"section": section, "before_bytes": len(old_chunk), "after_bytes": len(new_chunk), "before_sha256": hashlib.sha256(old_chunk).hexdigest(), "after_sha256": hashlib.sha256(new_chunk).hexdigest()})
    reparsed = ET.fromstring(output)
    require(semantically_equal(reparsed, draft), "Written XML differs from intended draft")
    semantic_diff = differences(original, reparsed)

    # Independent scope comparison: all root fields except the two cup records
    # and the seven emptied verification-version lists must be identical.
    restored = reparsed
    restored_basic_list = child(child(restored, "rule_group"), "cup_comps")
    restored_adv_list = child(restored, "files")
    for parent, current, old in [(restored_basic_list, find_comp(restored_basic_list, CUP_ID), original_basic), (restored_adv_list, find_comp(restored_adv_list, CUP_ID), original_advanced), (restored_adv_list, restored_adv_list[0], original_nation)]:
        position = list(parent).index(current)
        parent.remove(current)
        parent.insert(position, copy.deepcopy(old))
    current_verification = child(restored, "verf")
    position = list(restored).index(current_verification)
    restored.remove(current_verification)
    restored.insert(position, copy.deepcopy(original_verification))
    require(semantically_equal(restored, original), "A field outside the declared change scope changed")
    # Reverse the byte substitutions to prove that no other source bytes changed.
    reversed_output = output
    for before, after in reversed(replacements):
        old_chunk, new_chunk = as_bytes(before), as_bytes(after)
        require(reversed_output.count(new_chunk) == 1, "Output replacement is not uniquely reversible")
        reversed_output = reversed_output.replace(new_chunk, old_chunk, 1)
    require(reversed_output == source, "Bytes outside the four declared sections changed")

    log = {
        "status": "UNVERIFIED_EDITOR_PREVIEW",
        "source_sha256": SOURCE_SHA256,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "competition_id": CUP_ID,
        "changes": edits,
        "dates": date_changes,
        "round_progression": progression,
        "structural_diff": semantic_diff,
        "byte_replacements": replacement_log,
        "static_validation": {
            "xml_reparse": "passed", "original_input_unchanged": "checked by caller",
            "basic_advanced_counts_agree": "passed", "entrants": total_new,
            "matches": total_matches, "champions": previous_winners,
            "first_round_byes": 112, "rounds": 9,
            "wednesday_calendar_consistency": "passed for 2000/01 and 2025/26",
            "fa_cup_explicit_weekday_overlap": "none: unchanged FA Saturday, national cup Wednesday",
            "all_other_xml_sections_and_bytes": "unchanged", "db_changes": "unchanged",
            "verification_version_lists_cleared": 7,
        },
        "assumptions_and_runtime_checks": [
            "get_best_division_teams retains the source's select-all behavior when no number_teams field is present in advanced selectors.",
            "The first mixed-tier fixture uses the source's later mixed-round 100/100 marker; exact engine semantics require editor validation.",
            "The source already uses a three-day difference between advanced 2000/01 templates and basic 2025/26 dates. This preview preserves that first-season relationship; future-season recurrence remains untested.",
            "Existing date_range, first draw date, subsequent draw references, prizes, final rankings, priorities and cup types are preserved.",
            "No FM26 editor load, rules verification, season simulation or 20/30-year test has been run. Wednesday dates reduce explicit overlap but do not prove a conflict-free schedule.",
            "This preview changes cup eligibility and dates only. Player aging, ability loss, celebrity youth creation and face mapping remain unresolved.",
        ],
    }
    return output, log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Original Ver3 XML export (never overwritten)")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("women-celebrity-league-v3-cup400-unverified.xml"))
    parser.add_argument("--change-log", type=Path, default=Path(__file__).with_name("cup-change-log.json"))
    args = parser.parse_args()
    require(args.source.resolve() not in [args.output.resolve(), args.change_log.resolve()], "Output must not replace the input")
    require(args.output.resolve() != args.change_log.resolve(), "XML and change log paths must differ")
    source = args.source.read_bytes()
    output, log = build(source)
    require(args.source.read_bytes() == source, "Input changed during analysis")
    log_bytes = (json.dumps(log, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # Check both destinations before writing either artifact.
    for path, data in [(args.output, output), (args.change_log, log_bytes)]:
        require(not path.exists() or (path.is_file() and path.read_bytes() == data), f"Refusing to overwrite different existing data: {path}")
    write_once_or_identical(args.output, output)
    write_once_or_identical(args.change_log, log_bytes)
    print(json.dumps({"status": log["status"], "xml": str(args.output), "change_log": str(args.change_log), "sha256": log["output_sha256"], "semantic_differences": len(log["structural_diff"]), "entrants": 400, "matches": 399, "rounds": 9}, ensure_ascii=False))


if __name__ == "__main__":
    main()
