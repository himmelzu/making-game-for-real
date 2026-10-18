"""Pure planner tests: no live FM process is opened or modified."""

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("automation_planner", ROOT / "automation/fm26auto/planner.py")
PLANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLANNER)


def person(uid, name, dob="2003-02-28", female=True, random_id=None, **extra):
    born = dt.date.fromisoformat(dob)
    address = 0x100000 + uid * 0x1000
    result = {
        "uid": uid, "random_id": random_id if random_id is not None else uid + 10000,
        "row_id": uid + 99, "name": name, "full_name": name, "female": female,
        "dob": dob, "dob_raw": (born.year << 16) | born.timetuple().tm_yday,
        "ca": 120, "pa": 160, "person": address, "player": address - 0x288,
        "freeze_flags": 0, "name_refs": {
            "full": address + 0x1000, "first": address + 0x2000,
            "last": address + 0x3000, "common": address + 0x4000,
        },
    }
    result.update(extra)
    return result


def snapshot(*players, **extra):
    result = {"revision": "scan-1", "lease": {"module_sha256": "a" * 64, "pid": 22},
              "complete": True, "duplicates": [], "players": list(players)}
    result.update(extra)
    return result


CATALOG = [{"name": "연예인 가", "path": "회사/가.png", "sha256": "b" * 64},
           {"name": "연예인 나", "path": "회사/나.png", "sha256": "c" * 64}]
COHORT = [{"name": "연예인 가", "DOB_in_mod": "1995-02-28", "candidate_low32_id": "99999"},
          {"name": "연예인 나", "DOB_in_mod": "1997-05-10", "candidate_low32_id": "11111"}]


class AutomationPlannerTests(unittest.TestCase):
    def setUp(self):
        self.a = person(101, "연예인 가")
        self.b = person(102, "연예인 나", "2004-05-10")
        self.base = snapshot(self.a, self.b, person(103, "기존 유소년", "2009-07-01"))
        self.state = PLANNER.initialize(self.base, "2025-07-01", CATALOG, COHORT)

    def plan_with_youth(self, selected=None, **kwargs):
        youth = person(201, "New Name", "2010-01-01")
        scan = snapshot(self.a, self.b, youth)
        return PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, selected_keys=selected, **kwargs)

    def test_initialization_uses_names_birthdays_not_unreliable_database_ids(self):
        self.assertEqual(set(self.state["protected"]), {"101:10101", "102:10102"})
        self.assertIn("103:10103", self.state["seen"])
        self.assertEqual(self.state["donors"]["연예인 가"]["donor_key"], "101:10101")
        self.assertEqual(self.state["profile_hash"], "a" * 64)

    def test_state_is_json_serializable_and_contains_no_pointer_fields(self):
        serialized = json.dumps(self.state, ensure_ascii=False)
        for forbidden in ('"person"', '"player"', '"name_refs"', '"address"'):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn(str(self.a["person"]), serialized)

    def test_duplicate_cohort_name_and_birthday_is_not_protected(self):
        state = PLANNER.initialize(self.base, "2025-07-01", CATALOG, COHORT + [COHORT[0]])
        self.assertNotIn("101:10101", state["protected"])
        self.assertTrue(any("Ambiguous protected" in item for item in state["warnings"]))

    def test_same_name_distinct_players_not_used_as_donor(self):
        scan = snapshot(self.a, self.b, person(104, "연예인 가", "2001-01-01"))
        state = PLANNER.initialize(scan, "2025-07-01", CATALOG, COHORT)
        self.assertNotIn("연예인 가", state["donors"])

    def test_duplicate_catalog_names_or_photo_paths_are_excluded(self):
        for extra in (dict(CATALOG[0], path="회사/다른사진.png"),
                      dict(CATALOG[0], name="다른 이름")):
            state = PLANNER.initialize(self.base, "2025-07-01", CATALOG + [extra], COHORT)
            self.assertNotIn("연예인 가", state["donors"])

    def test_first_scan_and_preview_do_not_rename_existing_youth(self):
        result = PLANNER.plan(self.base, self.state, "2025-07-01", CATALOG)
        self.assertEqual(result["candidates"], [])
        self.assertFalse(any(item["label"].startswith("name.") for item in result["mutations"]))

    def test_preview_lists_new_candidate_but_keeps_it_unseen_unassigned(self):
        result = self.plan_with_youth()
        self.assertEqual(result["candidates"][0]["key"], "201:10201")
        self.assertTrue(result["candidates"][0]["requires_review"])
        self.assertEqual(result["next_state"]["assignments"], {})
        self.assertNotIn("201:10201", result["next_state"]["seen"])
        self.assertTrue(result["preview_only"])

    def test_selected_youth_gets_all_four_name_pointers_and_no_ability_or_dob_edits(self):
        result = self.plan_with_youth({"201:10201"})
        mutations = [item for item in result["mutations"] if item["uid"] == 201]
        youth = person(201, "New Name", "2010-01-01")
        self.assertEqual({item["address"] - youth["person"] for item in mutations},
                         {0x40, 0x50, 0x58, 0x60})
        self.assertEqual({item["label"] for item in mutations},
                         {"name.full", "name.first", "name.last", "name.common"})
        self.assertTrue(all(len(item["before"]) == len(item["after"]) == 8 for item in mutations))
        assignment = result["next_state"]["assignments"]["201:10201"]
        self.assertIn(assignment["name"], {"연예인 가", "연예인 나"})
        self.assertEqual(assignment["original_name"], "New Name")
        self.assertEqual(assignment["original_dob"], "2010-01-01")
        self.assertIn("201:10201", result["next_state"]["seen"])
        self.assertTrue(any("reference counting" in item for item in result["warnings"]))

    def test_nonselected_candidates_remain_candidates(self):
        scan = snapshot(self.a, self.b, person(201, "one", "2010-01-01"),
                        person(202, "two", "2011-01-01"))
        first = PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, {"201:10201"})
        second = PLANNER.plan(scan, first["next_state"], "2026-07-01", CATALOG)
        self.assertEqual([item["key"] for item in second["candidates"]], ["202:10202"])

    def test_ages_fourteen_through_eighteen_only_and_female_must_be_bool(self):
        extras = [person(201, "thirteen", "2013-07-02"),
                  person(202, "fourteen", "2012-07-01"),
                  person(203, "eighteen", "2007-07-02"),
                  person(204, "nineteen", "2007-07-01"),
                  person(205, "male", "2010-01-01", female=False),
                  person(206, "unknown", "2010-01-01", female=1),
                  person(207, "future", "2030-01-01")]
        result = PLANNER.plan(snapshot(self.a, self.b, *extras), self.state,
                              "2026-07-01", CATALOG)
        self.assertEqual([item["uid"] for item in result["candidates"]], [202, 203])

    def test_club_changes_do_not_affect_protection_or_assignments(self):
        moved = copy.deepcopy(self.a)
        moved["club"] = "different club"
        scan = snapshot(moved, self.b, person(201, "young", "2010-01-01", club="another club"))
        result = PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, {"201:10201"})
        self.assertEqual(len(result["next_state"]["assignments"]), 1)
        self.assertEqual(len(result["maintenance"]), 2)

    def test_profile_change_incomplete_duplicates_or_backwards_date_block_everything(self):
        bad_scans = [snapshot(self.a, self.b, complete=False),
                     snapshot(self.a, self.b, duplicates=[101]),
                     snapshot(self.a, self.b, self.a),
                     snapshot(self.a, self.b, lease={"module_sha256": "d" * 64})]
        for scan in bad_scans:
            with self.subTest(scan=scan), self.assertRaises(ValueError):
                PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, set())
        with self.assertRaisesRegex(ValueError, "backwards"):
            PLANNER.plan(self.base, self.state, "2025-06-30", CATALOG, set())

    def test_save_with_missing_or_wrong_random_id_anchor_is_blocked(self):
        for scan in (snapshot(self.a), snapshot(self.a, dict(self.b, random_id=444))):
            with self.subTest(scan=scan), self.assertRaisesRegex(ValueError, "anchor"):
                PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, set())

    def test_raw_freeze_bits_preserved_and_only_protected_players_modified(self):
        changed = dict(self.a, freeze_flags=0b10010001)
        scan = snapshot(changed, self.b, person(201, "young", "2010-01-01", freeze_flags=0x80))
        result = PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, set())
        freeze = [item for item in result["mutations"] if "freeze" in item["label"]]
        self.assertEqual({item["uid"] for item in freeze}, {101, 102})
        a = next(item for item in freeze if item["uid"] == 101)
        self.assertEqual(a["before"], b"\x91")
        self.assertEqual(a["after"], b"\xb1")
        self.assertEqual(a["address"], self.a["person"] + 0x132)

    def test_rejuvenation_preserves_dob_bits_and_is_really_age_twenty_two(self):
        changed = dict(self.a, dob_raw=self.a["dob_raw"] | 0xA600)
        result = PLANNER.plan(snapshot(changed, self.b), self.state, "2035-01-01", CATALOG, set())
        edit = next(item for item in result["mutations"]
                    if item["uid"] == 101 and "rejuvenate" in item["label"])
        raw = struct.unpack("<I", edit["after"])[0]
        self.assertEqual(raw & 0xFE00, 0xA600)
        born = dt.date(raw >> 16, 1, 1) + dt.timedelta(days=(raw & 0x1FF) - 1)
        self.assertEqual((born.month, born.day), (2, 28))
        self.assertEqual(PLANNER._age(born, dt.date(2035, 1, 1)), 22)
        self.assertEqual(edit["address"], self.a["person"] + 0x88)

    def test_february_29_is_clamped_when_target_year_is_not_leap(self):
        leap = person(101, "연예인 가", "2000-02-29")
        state = PLANNER.initialize(snapshot(leap), "2025-07-01", CATALOG,
                                   [{"name": "연예인 가", "DOB_in_mod": "1996-02-29"}])
        result = PLANNER.plan(snapshot(leap), state, "2031-03-01", CATALOG, set())
        raw = struct.unpack("<I", next(item for item in result["mutations"]
                                       if "rejuvenate" in item["label"])["after"])[0]
        self.assertEqual((raw >> 16, raw & 0x1FF), (2009, 59))

    def test_missing_dob_raw_skips_birthdate_write_and_warns(self):
        changed = dict(self.a)
        del changed["dob_raw"]
        result = PLANNER.plan(snapshot(changed, self.b), self.state, "2035-07-01", CATALOG, set())
        self.assertFalse(any(item["uid"] == 101 and "rejuvenate" in item["label"]
                             for item in result["mutations"]))
        self.assertTrue(any("dob_raw is missing" in item for item in result["warnings"]))

    def test_inconsistent_dob_raw_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "DOB date/raw mismatch"):
            PLANNER.plan(snapshot(dict(self.a, dob_raw=1), self.b), self.state,
                         "2035-07-01", CATALOG, set())

    def test_ability_floors_restore_declines_but_preserve_growth(self):
        a = dict(self.a, ca=100, pa=150)
        b = dict(self.b, ca=150, pa=180)
        result = PLANNER.plan(snapshot(a, b), self.state, "2026-07-01", CATALOG, set())
        edits = [item for item in result["mutations"] if "_floor" in item["label"]]
        self.assertEqual({item["label"] for item in edits}, {"protected.ca_floor", "protected.pa_floor"})
        self.assertTrue(all(item["uid"] == 101 for item in edits))
        self.assertEqual([struct.unpack("<H", item["after"])[0] for item in edits], [120, 160])

    def test_invalid_donors_or_changed_catalog_are_skipped(self):
        for scan, catalog in (
            (snapshot(dict(self.a, name_refs={}), dict(self.b, name_refs={}),
                      person(201, "young", "2010-01-01")), CATALOG),
            (snapshot(self.a, self.b, person(201, "young", "2010-01-01")), []),
        ):
            result = PLANNER.plan(scan, self.state, "2026-07-01", catalog, {"201:10201"})
            self.assertEqual(result["next_state"]["assignments"], {})
            self.assertNotIn("201:10201", result["next_state"]["seen"])

    def test_pointers_resolve_fresh_after_reload_and_never_persist(self):
        first = self.plan_with_youth({"201:10201"})
        state = first["next_state"]
        a, b = copy.deepcopy(self.a), copy.deepcopy(self.b)
        for donor in (a, b):
            donor["person"] += 0x100000000
            donor["player"] += 0x100000000
            donor["name_refs"] = {key: value + 0x100000000 for key, value in donor["name_refs"].items()}
        young = person(201, "young after reload", "2010-01-01")
        young["person"] += 0x200000000
        young["player"] += 0x200000000
        result = PLANNER.plan(snapshot(a, b, young), state, "2026-07-01", CATALOG, set())
        donor = a if state["assignments"]["201:10201"]["donor_key"] == "101:10101" else b
        names = [item for item in result["mutations"] if item["uid"] == 201]
        for item in names:
            field = item["label"].split(".")[1]
            self.assertEqual(struct.unpack("<Q", item["after"])[0], donor["name_refs"][field])
            self.assertGreater(item["address"], 0x200000000)

    def test_assignment_is_deterministic_and_no_input_data_is_mutated(self):
        scan = snapshot(self.a, self.b, person(201, "new", "2010-01-01"))
        state_before, scan_before = copy.deepcopy(self.state), copy.deepcopy(scan)
        a = PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, {"201:10201"})
        b = PLANNER.plan(scan, self.state, "2026-07-01", CATALOG, {"201:10201"})
        self.assertEqual(a, b)
        self.assertEqual(self.state, state_before)
        self.assertEqual(scan, scan_before)

    def test_thirty_seasons_keep_stable_assignments_and_need_no_new_photos(self):
        state = self.state
        live = {p["uid"]: copy.deepcopy(p) for p in self.base["players"]}
        identities = {}
        for season in range(1, 31):
            year = 2025 + season
            uid = 500 + season
            youth = person(uid, f"Youth {season}", f"{year - 16}-01-01")
            live[uid] = youth
            scan = snapshot(*live.values(), revision=f"season-{season}")
            result = PLANNER.plan(scan, state, f"{year}-07-01", CATALOG, {PLANNER._key(youth)})
            for item in result["mutations"]:
                p = live[item["uid"]]
                if item["label"].startswith("name."):
                    p["name_refs"][item["label"].split(".")[1]] = struct.unpack("<Q", item["after"])[0]
                elif item["label"] == "protected.freeze_attributes":
                    p["freeze_flags"] = item["after"][0]
                elif item["label"] == "protected.rejuvenate_to_22":
                    p["dob_raw"] = struct.unpack("<I", item["after"])[0]
                    p["dob"] = (dt.date(p["dob_raw"] >> 16, 1, 1)
                                + dt.timedelta(days=(p["dob_raw"] & 0x1FF) - 1)).isoformat()
            state = result["next_state"]
            for key, previous in identities.items():
                self.assertEqual(state["assignments"][key], previous)
            identities = copy.deepcopy(state["assignments"])
            self.assertLess(PLANNER._age(dt.date.fromisoformat(live[101]["dob"]),
                                       dt.date(year, 7, 1)), 28)
        self.assertEqual(len(state["assignments"]), 30)
        self.assertLessEqual(len({a["path"] for a in state["assignments"].values()}), 2)


if __name__ == "__main__":
    unittest.main()
