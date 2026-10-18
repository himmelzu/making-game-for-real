"""Pure, conservative plans for an explicitly applied FM26 runtime transaction.

This module never reads or writes a process.  Addresses exist only in returned
mutation plans, never in the persistent state.  The caller must independently
validate the process schema, pause state, lease, addresses and before-images.
The four name-pointer assignments remain an unverified runtime experiment.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import struct
import uuid
from collections import Counter, defaultdict


FORMAT = "fm26-auto-v1"
NAME_OFFSETS = {"full": 0x40, "first": 0x50, "last": 0x58, "common": 0x60}
NAME_WARNING = (
    "Name pointer reuse is experimental: FM string reference counting and lifetime "
    "have not been verified in a running game."
)


def _date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("A valid manual game_date (YYYY-MM-DD) is required") from exc


def _key(player):
    uid, random_id = player.get("uid"), player.get("random_id")
    if type(uid) is not int or not 0 < uid < 0xFFFFFFFF:
        raise ValueError("Invalid player UID")
    if type(random_id) is not int or not 0 <= random_id <= 0xFFFFFFFF:
        raise ValueError("Invalid player random_id")
    return f"{uid}:{random_id}"


def _age(born, today):
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _profile(snapshot):
    lease = snapshot.get("lease")
    profile = lease.get("module_sha256") if isinstance(lease, dict) else None
    if not isinstance(profile, str) or len(profile) != 64:
        raise ValueError("Missing module SHA256 profile")
    try:
        bytes.fromhex(profile)
    except ValueError as exc:
        raise ValueError("Invalid module SHA256 profile") from exc
    return profile.lower()


def _players(snapshot):
    if snapshot.get("complete") is not True:
        raise ValueError("Incomplete snapshot: no plan may be applied")
    if snapshot.get("duplicates"):
        raise ValueError("Duplicate UID candidates: no plan may be applied")
    if not isinstance(snapshot.get("revision"), str) or not snapshot["revision"]:
        raise ValueError("Snapshot revision is required")
    players = snapshot.get("players")
    if not isinstance(players, list):
        raise ValueError("Snapshot players must be a complete list")
    result = {}
    uids = set()
    for player in players:
        key = _key(player)
        if key in result or player["uid"] in uids:
            raise ValueError("Duplicate UID candidates: no plan may be applied")
        result[key] = player
        uids.add(player["uid"])
    return result


def _valid_born(player, today):
    try:
        born = _date(player.get("dob"))
    except ValueError:
        return None
    return born if 1900 <= born.year <= today.year and born <= today else None


def _catalog(catalog):
    """Only unambiguous names and paths may supply an identity."""
    rows = [dict(row) for row in catalog if isinstance(row, dict)]
    names = Counter(row.get("name") for row in rows)
    paths = Counter(row.get("path") for row in rows)
    result = {}
    for row in rows:
        name, path, digest = row.get("name"), row.get("path"), row.get("sha256")
        if not isinstance(name, str) or not name or names[name] != 1:
            continue
        if not isinstance(path, str) or not path or paths[path] != 1:
            continue
        if not isinstance(digest, str) or len(digest) != 64:
            continue
        try:
            bytes.fromhex(digest)
        except ValueError:
            continue
        result[name] = {"name": name, "path": path, "sha256": digest.lower()}
    return result


def _refs(player):
    refs = player.get("name_refs")
    if not isinstance(refs, dict):
        return None
    if any(type(refs.get(field)) is not int or not 0 <= refs[field] < 2**64
           for field in NAME_OFFSETS):
        return None
    # Empty common/last names are valid, but a full and a display name are needed.
    if not refs["full"] or not (refs["first"] or refs["common"]):
        return None
    return refs


def initialize(snapshot, game_date, catalog, cohort):
    """Capture all initially seen people and bind uniquely matching celebrities.

    Source database numeric IDs are deliberately ignored: this mod has ID shifts.
    An exact name and birth month/day are required for protected roster matching.
    """
    today = _date(game_date)
    players = _players(snapshot)
    profile = _profile(snapshot)
    by_identity = defaultdict(list)
    warnings = []
    for row in cohort:
        if not isinstance(row, dict):
            continue
        source_dob = row.get("DOB_in_mod", row.get("dob"))
        try:
            born = _date(source_dob)
        except ValueError:
            continue
        if row.get("name"):
            by_identity[(row["name"], born.month, born.day)].append(row)
    live_by_identity = defaultdict(list)
    by_name = defaultdict(list)
    for key, player in players.items():
        if player.get("female") is not True:
            continue
        born = _valid_born(player, today)
        if born is not None:
            live_by_identity[(player.get("name"), born.month, born.day)].append((key, player))
        by_name[player.get("name")].append((key, player))

    protected = {}
    for identity, matches in by_identity.items():
        live = live_by_identity.get(identity, [])
        if len(matches) != 1 or len(live) != 1:
            if live:
                warnings.append(f"Ambiguous protected identity skipped: {identity[0]}")
            continue
        key, player = live[0]
        if (type(player.get("ca")) is not int or not 1 <= player["ca"] <= 200
                or type(player.get("pa")) is not int or not 1 <= player["pa"] <= 200):
            continue
        protected[key] = {
            "uid": player["uid"], "random_id": player["random_id"],
            "name": player["name"], "dob": player["dob"],
            "ca": player["ca"], "pa": player["pa"],
        }
    donors = {}
    for name, row in _catalog(catalog).items():
        matches = by_name.get(name, [])
        if len(matches) == 1 and _refs(matches[0][1]) is not None:
            donors[name] = {"donor_key": matches[0][0], **row}
        elif matches:
            warnings.append(f"Ambiguous or invalid name donor skipped: {name}")
    return {
        "format": FORMAT, "career_id": str(uuid.uuid4()), "profile_hash": profile,
        "baseline_date": today.isoformat(), "last_date": today.isoformat(),
        "seen": sorted(players), "anchors": sorted(protected)[:20],
        "protected": protected, "donors": donors, "assignments": {},
        "warnings": warnings,
    }


def _mutation(player, address, before, after, label):
    if before == after:
        return None
    if type(address) is not int or address <= 0:
        raise ValueError("Invalid mutation address")
    return {"address": address, "before": before, "after": after,
            "uid": player["uid"], "random_id": player["random_id"], "label": label}


def _add(mutations, player, address, before, after, label):
    mutation = _mutation(player, address, before, after, label)
    if mutation is not None:
        mutations.append(mutation)


def _plan_names(target, donor, mutations):
    target_refs, donor_refs = _refs(target), _refs(donor)
    if target_refs is None or donor_refs is None:
        return False
    person = target.get("person")
    if type(person) is not int or person <= 0:
        return False
    for field, offset in NAME_OFFSETS.items():
        _add(mutations, target, person + offset,
             struct.pack("<Q", target_refs[field]), struct.pack("<Q", donor_refs[field]),
             f"name.{field}")
    return True


def _donor(binding, players, catalog):
    row = catalog.get(binding.get("name"))
    if row is None or any(row.get(k) != binding.get(k) for k in ("path", "sha256")):
        return None
    donor = players.get(binding.get("donor_key"))
    if (donor is None or donor.get("female") is not True
            or donor.get("name") != binding.get("name") or _refs(donor) is None):
        return None
    return donor


def _rejuvenated(born, today):
    year = today.year - 22
    day = born.day
    while True:
        try:
            result = dt.date(year, born.month, day)
            break
        except ValueError:
            day -= 1
    if (born.month, min(born.day, day)) > (today.month, today.day):
        year -= 1
        day = born.day
        while True:
            try:
                return dt.date(year, born.month, day)
            except ValueError:
                day -= 1
    return result


def _maintenance(player, base, today, mutations, warnings):
    if player.get("female") is not True:
        warnings.append(f"Protected player no longer validated as female: {_key(player)}")
        return
    born = _valid_born(player, today)
    person, player_addr = player.get("person"), player.get("player")
    if type(person) is not int or person <= 0 or type(player_addr) is not int or player_addr <= 0:
        raise ValueError("Invalid protected player address")
    flags = player.get("freeze_flags")
    if type(flags) is int and 0 <= flags <= 255:
        _add(mutations, player, person + 0x132, bytes([flags]), bytes([flags | 0x20]),
             "protected.freeze_attributes")
    else:
        warnings.append(f"Missing native freeze flags: {_key(player)}")
    for field, offset in (("ca", 0x264), ("pa", 0x266)):
        current = player.get(field)
        if type(current) is not int or not 1 <= current <= 200:
            raise ValueError("Invalid protected ability value")
        if current < base[field]:
            _add(mutations, player, player_addr + offset, struct.pack("<H", current),
                 struct.pack("<H", base[field]), f"protected.{field}_floor")
    if born is not None and _age(born, today) >= 28:
        raw = player.get("dob_raw")
        if type(raw) is not int or not 0 <= raw <= 0xFFFFFFFF:
            warnings.append(f"DOB unchanged because dob_raw is missing: {_key(player)}")
            return
        if (raw >> 16, raw & 0x1FF) != (born.year, born.timetuple().tm_yday):
            raise ValueError("DOB date/raw mismatch; refresh snapshot")
        new_born = _rejuvenated(born, today)
        after = (new_born.year << 16) | (raw & 0xFE00) | new_born.timetuple().tm_yday
        _add(mutations, player, person + 0x88, struct.pack("<I", raw),
             struct.pack("<I", after), "protected.rejuvenate_to_22")


def plan(snapshot, state, game_date, catalog, selected_keys=None, maintain=True):
    """Return an unapplied plan; ``None`` selection means identity preview only.

    A set (including an empty set) explicitly requests assignment-mode planning:
    selected new identities and existing assignments are reapplied.  Newcomers are
    first sightings, not proven FM-generated players, and are never auto-selected.
    Persist ``next_state`` only after the caller's complete transaction succeeds.
    """
    today = _date(game_date)
    players = _players(snapshot)
    if state.get("format") != FORMAT:
        raise ValueError("Unsupported persistent state format")
    if _profile(snapshot) != state.get("profile_hash"):
        raise ValueError("Module profile changed: revalidation is required")
    if today < _date(state["last_date"]) or today < _date(state["baseline_date"]):
        raise ValueError("Game date moved backwards: load the matching career state")
    for key in state.get("anchors", []):
        if key not in players or players[key].get("female") is not True:
            raise ValueError("Career anchor mismatch: do not apply this state to another save")
    selected = None if selected_keys is None else set(selected_keys)
    next_state = copy.deepcopy(state)
    next_state["last_date"] = today.isoformat()
    warnings = list(state.get("warnings", []))
    mutations, candidates, maintenance = [], [], []
    seen = set(state["seen"])
    current_catalog = _catalog(catalog)
    bindings = state.get("donors", {})
    available = {name: binding for name, binding in bindings.items()
                 if _donor(binding, players, current_catalog) is not None}
    candidate_by_key = {}
    for key, player in players.items():
        if key in seen or key in state.get("assignments", {}) or player.get("female") is not True:
            continue
        born = _valid_born(player, today)
        if born is None or not 14 <= _age(born, today) <= 18:
            continue
        candidate = {"key": key, "uid": player["uid"], "random_id": player["random_id"],
                     "name": player.get("name", ""), "dob": born.isoformat(),
                     "age": _age(born, today), "requires_review": True,
                     "classification": "first_sighting_youth_candidate"}
        candidates.append(candidate)
        candidate_by_key[key] = candidate
    candidates.sort(key=lambda item: (item["uid"], item["random_id"]))
    if selected is not None:
        unknown = selected - set(candidate_by_key) - set(state.get("assignments", {}))
        if unknown:
            raise ValueError("Selected keys are not current youth candidates or existing assignments")
        if not available and selected - set(state.get("assignments", {})):
            warnings.append("No uniquely bound, currently valid celebrity donors are available")
        # Stable identity reuse supports decades without requiring new photographs.
        names = sorted(available)
        for key in sorted(selected - set(state.get("assignments", {}))):
            if not names:
                break
            digest = hashlib.sha256((state["career_id"] + ":" + key).encode()).digest()
            name = names[int.from_bytes(digest[:8], "big") % len(names)]
            binding = available[name]
            target, donor = players[key], _donor(binding, players, current_catalog)
            if _plan_names(target, donor, mutations):
                next_state["assignments"][key] = {
                    "name": name, "path": binding["path"], "sha256": binding["sha256"],
                    "donor_key": binding["donor_key"], "original_name": target.get("name", ""),
                    "original_dob": target["dob"],
                }
                seen.add(key)
            else:
                warnings.append(f"Invalid target name references; candidate skipped: {key}")
        for key, assignment in state.get("assignments", {}).items():
            target = players.get(key)
            if target is None:
                warnings.append(f"Assigned player absent from snapshot: {key}")
                continue
            donor = _donor({"name": assignment["name"], **assignment}, players, current_catalog)
            if donor is None or target.get("female") is not True or not _plan_names(target, donor, mutations):
                warnings.append(f"Assigned player name restore skipped: {key}")
        if next_state["assignments"]:
            warnings.append(NAME_WARNING)
    if maintain:
        for key, base in state.get("protected", {}).items():
            player = players.get(key)
            if player is None:
                warnings.append(f"Protected player absent from snapshot: {key}")
                continue
            begin = len(mutations)
            _maintenance(player, base, today, mutations, warnings)
            maintenance.append({"key": key, "name": base["name"],
                                "mutation_count": len(mutations) - begin})
    next_state["seen"] = sorted(seen)
    return {"revision": snapshot["revision"], "lease": copy.deepcopy(snapshot["lease"]),
            "game_date": today.isoformat(), "candidates": candidates,
            "maintenance": maintenance, "mutations": mutations,
            "next_state": next_state, "warnings": list(dict.fromkeys(warnings)),
            "preview_only": selected_keys is None,
            "requires_candidate_review": bool(candidates)}
