#!/usr/bin/env python3
"""Index inspected person identity histories and align controlled player aliases.

Historical exports can contain several low UID values for one created identity.
This module preserves every UID and changes only Pnti/Pdob new_value nodes for
the explicitly reviewed 771-player cohort. It does not predict arbitrary game IDs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
COUNTRY_IDS = {135, 116, 110, 1435, 765, 769, 771, 796,
               784, 797, 786, 764, 776, 1651, 364, 390}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def value(node, key, default=None):
    child = node.find(f"*[@id='{key}']")
    return default if child is None else child.get("value", default)


def property_code(record):
    return int(value(record, "property")).to_bytes(4, "big").decode("ascii")


def _histories(root):
    db = root.find("list[@id='db_changes']")
    need(db is not None, "Missing database changes")
    histories, additions = defaultdict(list), defaultdict(list)
    for index, record in enumerate(db):
        table, code = value(record, "database_table_type"), property_code(record)
        if table == "1":
            uid = int(value(record, "db_unique_id"))
            histories[uid >> 32].append((index, record, uid, code))
        elif table == "55" and code == "ADDR":
            new = record.find("record[@id='new_value']")
            if new is not None and value(new, "database_table_type") == "1":
                uid = int(value(new, "db_unique_id"))
                additions[uid >> 32].append((index, new, uid))
    return db, histories, additions


def build_identity_index(root):
    """Return named, uniquely created female identities with explicit histories.

    Latest means the last record in XML order. A separate max-version result is
    recorded so consumers can detect disagreements, rather than assuming a
    proprietary importer precedence rule. No offset is applied here.
    """
    _, histories, additions = _histories(root)
    rows = []
    for high, records in histories.items():
        created = additions.get(high, [])
        if len(created) != 1 or value(created[0][1], "dcty") not in {"1", "2"}:
            continue
        names = [(i, r, u) for i, r, u, c in records if c == "Pcna"]
        genders = [(i, r) for i, r, _, c in records if c == "Pife"]
        if not names or not genders or value(genders[-1][1], "new_value") != "1":
            continue
        if any(c == "DELE" and value(r, "new_value") == "true"
               for _, r, _, c in records):
            continue
        last = records[-1]
        maximum_version = max(records, key=lambda x: (int(value(x[1], "version", "0")), x[0]))
        first_seen = {}
        for index, record, uid, code in records:
            first_seen.setdefault(uid, {"uid": str(uid), "low32": uid & 0xffffffff,
                                        "first_record_index": index,
                                        "first_property": code})
        latest_nation = next(((i, r, u) for i, r, u, c in reversed(records) if c == "Pnti"), None)
        rows.append({
            "name": value(names[-1][1], "new_value"),
            "source_object_high32": high,
            "addr_uid": str(created[0][2]),
            "addr_record_index": created[0][0],
            "name_record_uid": str(names[-1][2]),
            "earliest_record_uid": str(records[0][2]),
            "latest_record_uid": str(last[2]),
            "latest_record_low32": last[2] & 0xffffffff,
            "latest_record_index": last[0],
            "latest_record_property": last[3],
            "latest_record_version": int(value(last[1], "version", "0")),
            "max_version_record_uid": str(maximum_version[2]),
            "last_and_max_version_agree": last[2] == maximum_version[2],
            "latest_nationality_uid": str(latest_nation[2]) if latest_nation else None,
            "uid_history": list(first_seen.values()),
            "identity_evidence": "one nested person ADDR; female; named; no true deletion",
            "game_id_status": "historical XML candidate; requires save-specific calibration",
        })
    return sorted(rows, key=lambda r: (r["latest_record_low32"], r["name"]))


def build_photo_identity_index(root):
    """Index explicit female person identities for read-only photo matching.

    Photo subjects include copied people and a person named through Psna. This
    broader index deliberately leaves the controlled player patch's selection
    unchanged. Creation belongs to the nested ADDR/COPY target, never the COPY
    source. Every included person still needs one creation and explicit Pife=1.
    Low UIDs are source history values; save-specific calibration is external.
    """
    db, histories, _ = _histories(root)
    creations = defaultdict(list)
    for index, record in enumerate(db):
        table, code = value(record, "database_table_type"), property_code(record)
        if not ((table == "55" and code == "ADDR")
                or (table == "1" and code == "COPY")):
            continue
        new = record.find("record[@id='new_value']")
        if new is None or value(new, "database_table_type") != "1":
            continue
        uid = int(value(new, "db_unique_id"))
        creations[uid >> 32].append((index, new, uid, code,
                                     value(record, "db_unique_id")))

    rows = []
    for high, records in histories.items():
        created = creations.get(high, [])
        if len(created) != 1 or value(created[0][1], "dcty") not in {"1", "2"}:
            continue
        common_names = [(i, r, u, c) for i, r, u, c in records if c == "Pcna"]
        names = common_names or [(i, r, u, c) for i, r, u, c in records if c == "Psna"]
        genders = [(i, r, u) for i, r, u, c in records if c == "Pife"]
        if not names or not value(names[-1][1], "new_value") or not genders:
            continue
        if value(genders[-1][1], "new_value") != "1":
            continue
        if any(c == "DELE" and value(r, "new_value") == "true"
               for _, r, _, c in records):
            continue
        last = records[-1]
        maximum_version = max(records, key=lambda x: (int(value(x[1], "version", "0")), x[0]))
        first_seen = {}
        for index, record, uid, code in records:
            first_seen.setdefault(uid, {"uid": str(uid), "low32": uid & 0xffffffff,
                                        "first_record_index": index,
                                        "first_property": code})
        latest_nation = next(((i, r, u) for i, r, u, c in reversed(records) if c == "Pnti"), None)
        creation_index, creation, creation_uid, creation_code, source_uid = created[0]
        rows.append({
            "name": value(names[-1][1], "new_value"),
            "name_property": names[-1][3],
            "name_record_index": names[-1][0],
            "source_object_high32": high,
            "creation_property": creation_code,
            "creation_uid": str(creation_uid),
            "creation_record_index": creation_index,
            "creation_dcty": value(creation, "dcty"),
            "copy_source_uid": source_uid if creation_code == "COPY" else None,
            "addr_uid": str(creation_uid) if creation_code == "ADDR" else None,
            "addr_record_index": creation_index if creation_code == "ADDR" else None,
            "gender_record_index": genders[-1][0],
            "gender_record_uid": str(genders[-1][2]),
            "gender_value": "1",
            "name_record_uid": str(names[-1][2]),
            "earliest_record_uid": str(records[0][2]),
            "latest_record_uid": str(last[2]),
            "latest_record_low32": last[2] & 0xffffffff,
            "latest_record_index": last[0],
            "latest_record_property": last[3],
            "latest_record_version": int(value(last[1], "version", "0")),
            "max_version_record_uid": str(maximum_version[2]),
            "last_and_max_version_agree": last[2] == maximum_version[2],
            "latest_nationality_uid": str(latest_nation[2]) if latest_nation else None,
            "uid_history": list(first_seen.values()),
            "identity_evidence": f"one nested person {creation_code}; explicit Pife=1; {names[-1][3]}; no true deletion",
            "game_id_status": "historical XML candidate; requires save-specific calibration",
        })
    return sorted(rows, key=lambda r: (r["latest_record_low32"], r["name"]))


def _default_cohort():
    with (ROOT / "data/celebrity-cohort.csv").open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _payload(record):
    node = record.find("*[@id='new_value']")
    need(node is not None, "Missing new_value")
    return node


def _signature(node):
    return node.tag, dict(node.attrib), tuple(_signature(child) for child in node)


def patch_player_aliases(root, cohort=None):
    """Align all Pnti/Pdob histories to each reviewed player's intended values.

    Desired values come from the last Pnti/Pdob record keyed by the exact full
    cohort UID, which is the controlled v2 world patch. Never use the last alias
    as the desired nationality: it can still contain the original Korea edit.
    Validation is complete before any mutation, and unrelated records stay put.
    """
    cohort = _default_cohort() if cohort is None else list(cohort)
    need(len(cohort) == 771, "Expected the inspected 771-player cohort")
    uids = [int(person["person_uid"]) for person in cohort]
    need(len(set(uids)) == 771 and len({u >> 32 for u in uids}) == 771,
         "Cohort identities are not unique")
    db, histories, additions = _histories(root)
    planned, players, nations = [], [], Counter()
    for person, controlled_uid in zip(cohort, uids):
        high = controlled_uid >> 32
        created, records = additions.get(high, []), histories.get(high, [])
        need(len(created) == 1 and created[0][2] == controlled_uid
             and value(created[0][1], "dcty") in {"1", "2"},
             f"Ambiguous person creation for {person['name']}")
        need(not any(c == "COPY" or (c == "DELE" and value(r, "new_value") == "true")
                     for _, r, _, c in records), f"Copied or deleted identity: {person['name']}")
        exact = {c: (i, r) for i, r, u, c in records if u == controlled_uid}
        need("Pcna" in exact and value(exact["Pcna"][1], "new_value") == person["name"],
             "Cohort name mismatch")
        need("Pife" in exact and value(exact["Pife"][1], "new_value") == "1",
             "Cohort player is not explicitly female")
        desired = {}
        for code in ("Pnti", "Pdob"):
            need(code in exact, f"Missing controlled {code}")
            desired[code] = _payload(exact[code][1])
        nation = desired["Pnti"]
        need(nation.tag == "record", "Unexpected controlled nationality shape")
        dbid = int(value(nation, "DBID"))
        need(dbid in COUNTRY_IDS and int(value(nation, "Nnat")) == dbid * (2**32 + 1),
             "Controlled nationality is not a reviewed world assignment")
        nations[dbid] += 1
        dob = desired["Pdob"]
        need(dob.tag == "date", "Unexpected controlled birthdate shape")
        date(int(dob.get("year")), int(dob.get("month")), int(dob.get("day")))
        source = {code: {"record_index": exact[code][0], "uid": str(controlled_uid)}
                  for code in desired}
        indices = {"Pnti": [], "Pdob": []}
        for index, record, uid, code in records:
            if code not in desired:
                continue
            old = _payload(record)
            if code == "Pnti":
                need(old.tag == "null" or (old.tag == "record"
                     and value(old, "Nnat") is not None and value(old, "DBID") is not None),
                     "Unexpected alias nationality")
            else:
                need(old.tag == "date", "Unexpected alias birthdate")
            indices[code].append(index)
            if _signature(old) != _signature(desired[code]):
                planned.append((index, record, old, deepcopy(desired[code]), code, str(uid)))
        players.append({"name": person["name"], "source_object_high32": high,
                        "controlled_uid": str(controlled_uid),
                        "earliest_uid": str(records[0][2]), "latest_uid": str(records[-1][2]),
                        "all_uids": list(dict.fromkeys(str(u) for _, _, u, _ in records)),
                        "desired_source": source, "nation_dbid": dbid,
                        "birthdate": dict(dob.attrib), "record_indices": indices})
    need(set(nations) == COUNTRY_IDS and set(nations.values()) == {48, 49},
         "Input is not the reviewed 16-country world variant")
    # Only now mutate the completely validated tree, preserving IDs and metadata.
    changes = []
    for index, record, old, new, code, uid in planned:
        position = list(record).index(old)
        before = ET.tostring(old, encoding="unicode")
        new.tail = old.tail
        record.remove(old)
        record.insert(position, new)
        changes.append({"record_index": index, "uid": uid, "property": code,
                        "before": before, "after": ET.tostring(new, encoding="unicode")})
    return {"status": "STATIC_ALIAS_CONSISTENCY_PATCH_GAME_UNVERIFIED",
            "cohort_players": 771, "changed_records": len(changes),
            "changed_properties": dict(Counter(x["property"] for x in changes)),
            "desired_nationality_counts": dict(sorted(nations.items())),
            "identity_selection": "one exact cohort-matching nested ADDR per high32; no COPY/deletion",
            "desired_selection": "last exact-full-cohort-UID record per controlled property",
            "latest_uid_selection": "last person record in XML order; not an asserted compiler specification",
            "players": players, "changes": changes}
