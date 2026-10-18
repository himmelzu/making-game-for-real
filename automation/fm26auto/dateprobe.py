"""Read the FM26 world date through a unique native instruction signature.

Source facts (not copied executable code): poi82999/fm26rte commit
001cf00d1f1d509e5b6d440ebb1d27a45fb42726, research/cheat-table-tdg6661/
FMCET.CT lines 34631-34637 names the CURRENT GAME DATE pattern; decompiled
CETableDetails.lua:27 pins expectedModule to game_plugin.dll.  The instruction
``0F B7 05 disp32`` references datTimeRoot directly at RIP + signed disp32.
FMCETableHelpers.lua:1028-1074 reads day-of-year at that address (mask 0x1ff)
and year at address+2.  This is distinct from a team's next fixture date.

This read-only probe does not certify any write offsets or a game version.  Its
result is experimental until checked against the displayed date in live FM26.
No best-match, next-match, cohort or operating-system date fallback is used.
"""

from __future__ import annotations

import datetime as dt
import struct


CHUNK_SIZE = 4 * 1024 * 1024
MAX_MODULE_SIZE = 2 * 1024 * 1024 * 1024
MIN_ADDRESS = 0x10000
MAX_ADDRESS = 0x00007FFFFFFFFFFF
PREFIX = b"\x0f\xb7\x05"
SUFFIX = b"\x66\x41\x33\x87"
PATTERN_SIZE = 11


class DateProbeError(ValueError):
    """The unique, stable native game-date location could not be established."""


def _read(memory, address, size):
    if (type(address) is not int or type(size) is not int or size <= 0
            or address < MIN_ADDRESS or address + size - 1 > MAX_ADDRESS):
        raise DateProbeError("Invalid game-date read address or size")
    try:
        data = memory.read(address, size)
    except Exception as exc:
        raise DateProbeError("Cannot read the game-date module or static value") from exc
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) != size:
        raise DateProbeError("Incomplete game-date memory read")
    return bytes(data)


def _module(modules):
    matches = [module for module in modules
               if isinstance(module, dict)
               and isinstance(module.get("name"), str)
               and module["name"].casefold() == "game_plugin.dll"]
    if len(matches) != 1:
        raise DateProbeError("Exactly one game_plugin.dll module is required")
    module = matches[0]
    base, size = module.get("base"), module.get("size")
    if (type(base) is not int or type(size) is not int or base < MIN_ADDRESS
            or size < PATTERN_SIZE or size > MAX_MODULE_SIZE
            or base + size - 1 > MAX_ADDRESS):
        raise DateProbeError("Invalid game_plugin.dll range (maximum 2 GiB)")
    return base, size


def _find_hits(memory, base, size):
    hits = set()
    offset = 0
    overlap = PATTERN_SIZE - 1
    while offset < size:
        length = min(CHUNK_SIZE, size - offset)
        chunk = _read(memory, base + offset, length)
        search_at = 0
        while True:
            found = chunk.find(PREFIX, search_at)
            if found < 0:
                break
            if (found + PATTERN_SIZE <= len(chunk)
                    and chunk[found + 7:found + PATTERN_SIZE] == SUFFIX):
                hits.add(base + offset + found)
                if len(hits) > 1:
                    raise DateProbeError("Multiple current-date signatures; no location selected")
            search_at = found + 1
        if offset + length == size:
            break
        offset += length - overlap
    if len(hits) != 1:
        raise DateProbeError("Unique current-date signature was not found")
    return next(iter(hits))


def find_current_date(memory, modules):
    """Return {date,address,raw,source} from a stable, unique AOB match.

    ``memory.read(address, size)`` must return exactly that many bytes; at most
    4 MiB is requested per call.  ``modules`` contains {name,base,size} rows.
    Any unreadable module chunk, malformed value, unstable read or ambiguous
    signature fails closed with DateProbeError.  The returned address is a
    static 4-byte value, not a pointer slot, and must never be written here.
    """
    base, size = _module(modules)
    hit = _find_hits(memory, base, size)
    instruction = _read(memory, hit, PATTERN_SIZE)
    if instruction[:3] != PREFIX or instruction[7:] != SUFFIX:
        raise DateProbeError("Current-date instruction changed during the scan")
    displacement = struct.unpack("<i", instruction[3:7])[0]
    target = hit + 7 + displacement
    if not MIN_ADDRESS <= target <= MAX_ADDRESS - 3:
        raise DateProbeError("Current-date RIP target is not a plausible address")
    # A RIP-relative static referenced by this module should be in its image.
    if not base <= target <= base + size - 4:
        raise DateProbeError("Current-date static target is outside game_plugin.dll")
    first = _read(memory, target, 4)
    second = _read(memory, target, 4)
    if first != second:
        raise DateProbeError("Current game date changed while reading; pause and rescan")
    raw = struct.unpack("<I", first)[0]
    year, day = raw >> 16, raw & 0x1FF
    if not 1900 <= year <= 2200 or day == 0:
        raise DateProbeError("Current game date has an invalid year or day")
    date = dt.date(year, 1, 1) + dt.timedelta(days=day - 1)
    if date.year != year:
        raise DateProbeError("Current game date has an invalid day for its year")
    return {"date": date.isoformat(), "address": target, "raw": raw,
            "source": "exact-aob"}
