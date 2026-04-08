#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SLOT_SIZE = 2160
PLAYER_SLOTS = 3
NAME_CHARS = 16
CAREER_COUNT = 40

class ParseError(Exception):
    pass

def read_u8(buf: bytes, off: int) -> tuple[int, int]:
    return buf[off], off + 1

def read_u32(buf: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from('<I', buf, off)[0], off + 4

def read_i32(buf: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from('<i', buf, off)[0], off + 4

def read_f32(buf: bytes, off: int) -> tuple[float, int]:
    return struct.unpack_from('<f', buf, off)[0], off + 4

def read_bool8(buf: bytes, off: int) -> tuple[bool, int]:
    v = buf[off]
    if v not in (0, 1):
        raise ParseError(f'Expected bool byte at 0x{off:X}, got 0x{v:02X}')
    return bool(v), off + 1

def read_utf16le_fixed(buf: bytes, off: int, chars: int) -> tuple[str, int]:
    raw = buf[off:off + chars * 2]
    text = raw.decode('utf-16le', errors='ignore').split('\x00', 1)[0]
    return text, off + chars * 2

def set_text(elem: ET.Element, path: str, value: object) -> None:
    found = elem.find(path)
    if found is None:
        raise ParseError(f'Missing XML path in template: {path}')
    if isinstance(value, bool):
        found.text = 'true' if value else 'false'
    else:
        found.text = str(value)

def parse_known_player_prefix(slot: bytes) -> dict[str, object]:
    off = 0
    out: dict[str, object] = {}

    out['m_playerName'], off = read_utf16le_fixed(slot, off, NAME_CHARS)
    out['m_volumeSfx'], off = read_f32(slot, off)
    out['m_volumeMusic'], off = read_f32(slot, off)
    out['m_avatarID'], off = read_i32(slot, off)
    out['m_selectedCar'], off = read_i32(slot, off)
    out['m_cash'], off = read_i32(slot, off)
    out['m_lastPlayedMusic'], off = read_i32(slot, off)
    out['m_timeSeed'], off = read_i32(slot, off)

    out['m_carColor'] = []
    for _ in range(7):
        v, off = read_i32(slot, off)
        out['m_carColor'].append(v)

    out['m_carPurchased'] = []
    for _ in range(7):
        v, off = read_bool8(slot, off)
        out['m_carPurchased'].append(v)

    out['m_isMPH'], off = read_bool8(slot, off)

    for key in ('m_carDamage', 'm_carFuel', 'm_carNitro', 'm_carRadar'):
        arr = []
        for _ in range(7):
            v, off = read_f32(slot, off)
            arr.append(v)
        out[key] = arr

    for key in ('m_carEngine', 'm_carHandling', 'm_carSteering', 'm_carGearBox'):
        arr = []
        for _ in range(7):
            v, off = read_i32(slot, off)
            arr.append(v)
        out[key] = arr

    out['m_missionFired'] = []
    for _ in range(24):
        v, off = read_bool8(slot, off)
        out['m_missionFired'].append(v)

    out['m_career'] = []
    for _ in range(CAREER_COUNT):
        v, off = read_u8(slot, off)
        out['m_career'].append(v)

    out['_parsed_prefix_end'] = off
    return out

def apply_array(container: ET.Element, values: list[object], name: str) -> None:
    if len(container) != len(values):
        raise ParseError(
            f'Template node {name} has {len(container)} children, expected {len(values)}'
        )
    for child, value in zip(container, values):
        if isinstance(value, bool):
            child.text = 'true' if value else 'false'
        else:
            child.text = str(value)

def apply_known_fields(player_elem: ET.Element, data: dict[str, object]) -> None:
    set_text(player_elem, 'm_playerName', data['m_playerName'])
    set_text(player_elem, 'm_volumeSfx', data['m_volumeSfx'])
    set_text(player_elem, 'm_volumeMusic', data['m_volumeMusic'])
    set_text(player_elem, 'm_avatarID', data['m_avatarID'])
    set_text(player_elem, 'm_selectedCar', data['m_selectedCar'])
    set_text(player_elem, 'm_cash', data['m_cash'])
    set_text(player_elem, 'm_lastPlayedMusic', data['m_lastPlayedMusic'])
    set_text(player_elem, 'm_timeSeed', data['m_timeSeed'])
    set_text(player_elem, 'm_isMPH', data['m_isMPH'])

    for container_name in (
        'm_carColor', 'm_carPurchased', 'm_carDamage', 'm_carFuel',
        'm_carNitro', 'm_carRadar', 'm_carEngine', 'm_carHandling',
        'm_carSteering', 'm_carGearBox', 'm_missionFired', 'm_career',
    ):
        container = player_elem.find(container_name)
        if container is None:
            raise ParseError(f'Missing XML path in template: {container_name}')
        apply_array(container, data[container_name], container_name)

def indent(elem: ET.Element, level: int = 0) -> None:
    i = '\n' + level * '  '
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + '  '
        for child in elem:
            indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    elif level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i

def convert(three_ds_path: Path, template_xml_path: Path, output_xml_path: Path) -> None:
    blob = three_ds_path.read_bytes()
    min_size = PLAYER_SLOTS * SLOT_SIZE
    if len(blob) < min_size:
        raise ParseError(f'3DS save is too small: {len(blob)} bytes')

    root = ET.parse(template_xml_path).getroot()
    players = root.find('m_players')
    if players is None or len(players) != PLAYER_SLOTS:
        raise ParseError('Template XML must contain exactly 3 player slots under <m_players>.')

    template_players = [copy.deepcopy(p) for p in players]
    players.clear()

    for slot_index in range(PLAYER_SLOTS):
        start = slot_index * SLOT_SIZE
        end = start + SLOT_SIZE
        slot = blob[start:end]
        parsed = parse_known_player_prefix(slot)

        player_elem = copy.deepcopy(template_players[slot_index])
        apply_known_fields(player_elem, parsed)
        players.append(player_elem)

    if len(blob) >= min_size + 4:
        selected_id = struct.unpack_from('<I', blob, min_size)[0]
        set_text(root, 'm_selectedID', selected_id)

    indent(root)
    xml_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    output_xml_path.write_bytes(xml_bytes)

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Convert an 80s Overdrive 3DS binary save into a PC-style XML save using a PC XML template.'
    )
    parser.add_argument('three_ds_save', type=Path, help='Path to the 3DS binary save')
    parser.add_argument('template_xml', type=Path, help='Path to a valid PC XML save used as a template')
    parser.add_argument('output_xml', type=Path, help='Path to write the converted PC XML save')
    args = parser.parse_args()

    try:
        convert(args.three_ds_save, args.template_xml, args.output_xml)
    except ParseError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

    print(f'Wrote converted save to: {args.output_xml}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
