#!/usr/bin/env python3
"""Package one existing cumulative Editor XML and a plain-text Korean guide.

This is a data-only repack of the inspected v2 world variant, not a new FMF
compiler, international-rules patch, or runtime automation program.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MEMBER = "editor/longplay_world_experimental.xml"
XML_NAME = "FM26_unified.xml"
GUIDE_NAME = "적용방법.txt"
EXPECTED_XML_SHA256 = "247c644252d2e1312c579bd6633a26788163e5d027d888f193931621d762963b"
EXPECTED_CUPS_SHA256 = "837d7ab4aa077a909fee84ec33c341395f22fcbb6a14df2d293c5e247cab9fd6"
COUNTRY_IDS = {135, 116, 110, 1435, 765, 769, 771, 796, 784, 797, 786, 764, 776, 1651, 364, 390}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def value(record, key):
    child = record.find(f"*[@id='{key}']")
    need(child is not None, f"Missing {key}")
    return child.get("value")


def validate(xml, cups, cohort):
    """Check identity, cumulative fields, and preservation without invoking FM."""
    need(digest(xml) == EXPECTED_XML_SHA256, "Unexpected v2 world XML; refusing to repack")
    need(digest(cups) == EXPECTED_CUPS_SHA256, "Unexpected v2 cups XML")
    root, original = ET.fromstring(xml), ET.fromstring(cups)
    db, original_db = root.find("list[@id='db_changes']"), original.find("list[@id='db_changes']")
    need(db is not None and original_db is not None and len(db) == len(original_db) == 54726,
         "Unexpected database-change count")
    need(len(cohort) == 771 and len({p["person_uid"] for p in cohort}) == 771, "Unexpected celebrity cohort")
    cohort_ids = {p["person_uid"] for p in cohort}
    changes, latest = Counter(), {}
    for before, after in zip(original_db, db):
        if value(after, "database_table_type") == "1":
            code = int(value(after, "property")).to_bytes(4, "big").decode("ascii")
            latest[value(after, "db_unique_id"), code] = after.find("*[@id='new_value']")
        if ET.tostring(before) == ET.tostring(after):
            continue
        need(value(after, "database_table_type") == "1" and value(after, "db_unique_id") in cohort_ids,
             "Change outside inspected players")
        code = int(value(after, "property")).to_bytes(4, "big").decode("ascii")
        need(code in {"Pdob", "Pnti"}, "Unexpected changed player field")
        changes[code] += 1
        for node in (before, after):
            new_value = node.find("*[@id='new_value']")
            need(new_value is not None, "Missing changed value")
            node.remove(new_value)
        need(ET.tostring(before) == ET.tostring(after), "Unexpected record metadata change")
    need(changes == Counter(Pdob=666, Pnti=771), "Unexpected cumulative player changes")
    # Independent root comparison checks every rule and competition against cups.
    original.remove(original_db)
    root.remove(db)
    need(ET.tostring(original) == ET.tostring(root), "Rules differ from the cups variant")
    nations = Counter(int(value(latest[uid, "Pnti"], "DBID")) for uid in cohort_ids)
    need(set(nations) == COUNTRY_IDS and set(nations.values()) == {48, 49}, "Unexpected nationality distribution")
    rule_group = root.find("record[@id='rule_group']")
    need(len(rule_group.find("list[@id='league_comps']")) == 6, "Unexpected league levels")
    need(len(rule_group.find("list[@id='cup_comps']")) == 8, "Unexpected cup count")
    need(len(root.find("list[@id='files']")) == 39, "Unexpected advanced-rule files")
    need(root.find("null[@id='international_rule_group']") is not None,
         "Unexpected international rule group")
    return {"db_changes": 54726, "initial_age_cohort": 771, "birthdate_records_changed": 666,
            "primary_nationalities_changed": 771, "nationality_counts_by_dbid": dict(sorted(nations.items())),
            "league_levels": 6, "cup_rules_total": 8, "additional_cups": 3,
            "rules_identical_to_cups_variant": True,
            "fm26_editor_and_game_verified": False}


def build(source, output, guide):
    with zipfile.ZipFile(source) as archive:
        need(archive.testzip() is None, "Source ZIP CRC error")
        need(archive.namelist().count(MEMBER) == 1, "Duplicate or missing world XML")
        xml = archive.read(MEMBER)
        cups = archive.read("editor/cups.xml")
    with (ROOT / "data/celebrity-cohort.csv").open(encoding="utf-8-sig", newline="") as file:
        report = validate(xml, cups, list(csv.DictReader(file)))
    guide_text = guide.read_text(encoding="utf-8")
    # The downloadable guide is readable in Windows Notepad, including older versions.
    guide_bytes = guide_text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in [(XML_NAME, xml), (GUIDE_NAME, guide_bytes)]:
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 18, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, body, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    result = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        need(archive.testzip() is None, "Output ZIP CRC error")
        need(archive.namelist() == [XML_NAME, GUIDE_NAME], "Unexpected delivery contents")
        need(archive.read(XML_NAME) == xml, "Packaged XML differs")
        need(archive.read(GUIDE_NAME) == guide_bytes, "Packaged guide differs")
    # Keep the original v2 ZIP and any different existing output untouched.
    need(source.resolve() != output.resolve(), "Cannot overwrite source")
    need(not output.exists() or output.read_bytes() == result, "Different output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    return {"file": str(output), "bytes": len(result), "sha256": digest(result),
            "xml_bytes": len(xml), "xml_sha256": digest(xml),
            "members": [XML_NAME, GUIDE_NAME], "static_verification": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "downloads/fm26-longplay-v2.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "downloads/fm26-unified-data.zip")
    parser.add_argument("--guide", type=Path, default=ROOT / "docs/unified-editor.md")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.guide), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
