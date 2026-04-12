
import copy
import struct
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
import xml.etree.ElementTree as ET
from xml.dom import minidom

# This is the SaveGame decryption key for the XOR Cipher they used.
KEY = b"PLEASE DO NOT HACK THIS FOR A WHILE. WE REALLY WANT TO EARN A BIT."
REQUIRED_TAG = "m_isMPH"

SLOT_SIZE = 2160
PLAYER_SLOTS = 3
NAME_CHARS = 16
CAR_COUNT = 7
MISSION_COUNT = 24
CAREER_COUNT = 40

class ParseError(Exception):
    pass

def xor_data(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def try_decode_xml(data: bytes) -> str:
    encodings = ["utf-8", "utf-16", "utf-16-le", "utf-16-be", "latin-1"]
    for enc in encodings:
        try:
            text = data.decode(enc)
            if "<" in text and ">" in text:
                return text
        except UnicodeDecodeError:
            pass
    raise ValueError("Could not decode data as XML text.")

def pretty_xml(element: ET.Element) -> str:
    rough_string = ET.tostring(element, encoding="utf-8")
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="    ")

def validate_80s_overdrive_save(xml_text: str) -> ET.Element:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"XML parse failed: {e}") from e

    if REQUIRED_TAG not in xml_text:
        raise ValueError(
            f"This SaveGame does not appear to be a valid 80s Overdrive Save. "
            f"Required tag '{REQUIRED_TAG}' was not found."
        )

    return root

def read_u8(buf: bytes, off: int) -> tuple[int, int]:
    return buf[off], off + 1

def read_u32(buf: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from("<I", buf, off)[0], off + 4

def read_i32(buf: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from("<i", buf, off)[0], off + 4

def read_f32(buf: bytes, off: int) -> tuple[float, int]:
    return struct.unpack_from("<f", buf, off)[0], off + 4

def read_bool8(buf: bytes, off: int) -> tuple[bool, int]:
    v = buf[off]
    if v not in (0, 1):
        raise ParseError(f"Expected bool byte at 0x{off:X}, got 0x{v:02X}")
    return bool(v), off + 1

def read_utf16le_fixed(buf: bytes, off: int, chars: int) -> tuple[str, int]:
    raw = buf[off:off + chars * 2]
    text = raw.decode("utf-16le", errors="ignore").split("\x00", 1)[0]
    return text, off + chars * 2

def write_u8(buf: bytearray, off: int, value: int) -> int:
    struct.pack_into("<B", buf, off, int(value) & 0xFF)
    return off + 1

def write_u32(buf: bytearray, off: int, value: int) -> int:
    struct.pack_into("<I", buf, off, int(value))
    return off + 4

def write_i32(buf: bytearray, off: int, value: int) -> int:
    struct.pack_into("<i", buf, off, int(value))
    return off + 4

def write_f32(buf: bytearray, off: int, value: float) -> int:
    struct.pack_into("<f", buf, off, float(value))
    return off + 4

def write_bool8(buf: bytearray, off: int, value: bool) -> int:
    struct.pack_into("<B", buf, off, 1 if bool(value) else 0)
    return off + 1

def write_utf16le_fixed(buf: bytearray, off: int, chars: int, value: str) -> int:
    text = (value or "")[:chars]
    encoded = text.encode("utf-16le")
    field_size = chars * 2
    padded = encoded[:field_size] + (b"\x00" * max(0, field_size - len(encoded[:field_size])))
    buf[off:off + field_size] = padded
    return off + field_size

def set_text(elem: ET.Element, path: str, value: object) -> None:
    found = elem.find(path)
    if found is None:
        raise ParseError(f"Missing XML path in template: {path}")
    if isinstance(value, bool):
        found.text = "true" if value else "false"
    else:
        found.text = str(value)

def parse_known_player_prefix(slot: bytes) -> dict[str, object]:
    off = 0
    out: dict[str, object] = {}

    out["m_playerName"], off = read_utf16le_fixed(slot, off, NAME_CHARS)
    out["m_volumeSfx"], off = read_f32(slot, off)
    out["m_volumeMusic"], off = read_f32(slot, off)
    out["m_avatarID"], off = read_i32(slot, off)
    out["m_selectedCar"], off = read_i32(slot, off)
    out["m_cash"], off = read_i32(slot, off)
    out["m_lastPlayedMusic"], off = read_i32(slot, off)
    out["m_timeSeed"], off = read_i32(slot, off)

    out["m_carColor"] = []
    for _ in range(CAR_COUNT):
        v, off = read_i32(slot, off)
        out["m_carColor"].append(v)

    out["m_carPurchased"] = []
    for _ in range(CAR_COUNT):
        v, off = read_bool8(slot, off)
        out["m_carPurchased"].append(v)

    out["m_isMPH"], off = read_bool8(slot, off)

    for key in ("m_carDamage", "m_carFuel", "m_carNitro", "m_carRadar"):
        arr = []
        for _ in range(CAR_COUNT):
            v, off = read_f32(slot, off)
            arr.append(v)
        out[key] = arr

    for key in ("m_carEngine", "m_carHandling", "m_carSteering", "m_carGearBox"):
        arr = []
        for _ in range(CAR_COUNT):
            v, off = read_i32(slot, off)
            arr.append(v)
        out[key] = arr

    out["m_missionFired"] = []
    for _ in range(MISSION_COUNT):
        v, off = read_bool8(slot, off)
        out["m_missionFired"].append(v)

    out["m_career"] = []
    for _ in range(CAREER_COUNT):
        v, off = read_u8(slot, off)
        out["m_career"].append(v)

    out["_parsed_prefix_end"] = off
    return out

def apply_array(container: ET.Element, values: list[object], name: str) -> None:
    if len(container) != len(values):
        raise ParseError(
            f"Template node {name} has {len(container)} children, expected {len(values)}"
        )
    for child, value in zip(container, values):
        if isinstance(value, bool):
            child.text = "true" if value else "false"
        else:
            child.text = str(value)

def apply_known_fields(player_elem: ET.Element, data: dict[str, object]) -> None:
    set_text(player_elem, "m_playerName", data["m_playerName"])
    set_text(player_elem, "m_volumeSfx", data["m_volumeSfx"])
    set_text(player_elem, "m_volumeMusic", data["m_volumeMusic"])
    set_text(player_elem, "m_avatarID", data["m_avatarID"])
    set_text(player_elem, "m_selectedCar", data["m_selectedCar"])
    set_text(player_elem, "m_cash", data["m_cash"])
    set_text(player_elem, "m_lastPlayedMusic", data["m_lastPlayedMusic"])
    set_text(player_elem, "m_timeSeed", data["m_timeSeed"])
    set_text(player_elem, "m_isMPH", data["m_isMPH"])

    for container_name in (
        "m_carColor", "m_carPurchased", "m_carDamage", "m_carFuel",
        "m_carNitro", "m_carRadar", "m_carEngine", "m_carHandling",
        "m_carSteering", "m_carGearBox", "m_missionFired", "m_career",
    ):
        container = player_elem.find(container_name)
        if container is None:
            raise ParseError(f"Missing XML path in template: {container_name}")
        apply_array(container, data[container_name], container_name)


def parse_bool_text(text: str) -> bool:
    value = (text or "").strip().lower()
    if value in ("true", "1"):
        return True
    if value in ("false", "0", ""):
        return False
    raise ParseError(f"Expected boolean text, got: {text!r}")


def parse_array_values(container: ET.Element, expected_len: int, kind: str, name: str) -> list[object]:
    if container is None:
        raise ParseError(f"Missing XML path in save: {name}")
    if len(container) != expected_len:
        raise ParseError(f"XML node {name} has {len(container)} children, expected {expected_len}")

    out = []
    for child in container:
        text = child.text or ""
        if kind == "bool":
            out.append(parse_bool_text(text))
        elif kind == "i32":
            out.append(int(text.strip() or "0"))
        elif kind == "u8":
            value = int(text.strip() or "0")
            if not 0 <= value <= 255:
                raise ParseError(f"Value {value} out of range for {name}")
            out.append(value)
        elif kind == "f32":
            out.append(float(text.strip() or "0"))
        else:
            raise ParseError(f"Unsupported array kind: {kind}")
    return out

def extract_known_fields_from_xml(player_elem: ET.Element) -> dict[str, object]:
    def get_text(path: str) -> str:
        found = player_elem.find(path)
        if found is None:
            raise ParseError(f"Missing XML path in save: {path}")
        return found.text or ""

    out: dict[str, object] = {}
    out["m_playerName"] = get_text("m_playerName")
    out["m_volumeSfx"] = float(get_text("m_volumeSfx").strip() or "0")
    out["m_volumeMusic"] = float(get_text("m_volumeMusic").strip() or "0")
    out["m_avatarID"] = int(get_text("m_avatarID").strip() or "0")
    out["m_selectedCar"] = int(get_text("m_selectedCar").strip() or "0")
    out["m_cash"] = int(get_text("m_cash").strip() or "0")
    out["m_lastPlayedMusic"] = int(get_text("m_lastPlayedMusic").strip() or "0")
    out["m_timeSeed"] = int(get_text("m_timeSeed").strip() or "0")
    out["m_isMPH"] = parse_bool_text(get_text("m_isMPH"))

    out["m_carColor"] = parse_array_values(player_elem.find("m_carColor"), CAR_COUNT, "i32", "m_carColor")
    out["m_carPurchased"] = parse_array_values(player_elem.find("m_carPurchased"), CAR_COUNT, "bool", "m_carPurchased")
    out["m_carDamage"] = parse_array_values(player_elem.find("m_carDamage"), CAR_COUNT, "f32", "m_carDamage")
    out["m_carFuel"] = parse_array_values(player_elem.find("m_carFuel"), CAR_COUNT, "f32", "m_carFuel")
    out["m_carNitro"] = parse_array_values(player_elem.find("m_carNitro"), CAR_COUNT, "f32", "m_carNitro")
    out["m_carRadar"] = parse_array_values(player_elem.find("m_carRadar"), CAR_COUNT, "f32", "m_carRadar")
    out["m_carEngine"] = parse_array_values(player_elem.find("m_carEngine"), CAR_COUNT, "i32", "m_carEngine")
    out["m_carHandling"] = parse_array_values(player_elem.find("m_carHandling"), CAR_COUNT, "i32", "m_carHandling")
    out["m_carSteering"] = parse_array_values(player_elem.find("m_carSteering"), CAR_COUNT, "i32", "m_carSteering")
    out["m_carGearBox"] = parse_array_values(player_elem.find("m_carGearBox"), CAR_COUNT, "i32", "m_carGearBox")
    out["m_missionFired"] = parse_array_values(player_elem.find("m_missionFired"), MISSION_COUNT, "bool", "m_missionFired")
    out["m_career"] = parse_array_values(player_elem.find("m_career"), CAREER_COUNT, "u8", "m_career")
    return out

def build_3ds_slot_from_xml(original_slot: bytes, player_elem: ET.Element) -> bytes:
    if len(original_slot) != SLOT_SIZE:
        raise ParseError(f"3DS slot size mismatch: expected {SLOT_SIZE}, got {len(original_slot)}")

    data = extract_known_fields_from_xml(player_elem)
    slot = bytearray(original_slot)
    off = 0

    off = write_utf16le_fixed(slot, off, NAME_CHARS, data["m_playerName"])
    off = write_f32(slot, off, data["m_volumeSfx"])
    off = write_f32(slot, off, data["m_volumeMusic"])
    off = write_i32(slot, off, data["m_avatarID"])
    off = write_i32(slot, off, data["m_selectedCar"])
    off = write_i32(slot, off, data["m_cash"])
    off = write_i32(slot, off, data["m_lastPlayedMusic"])
    off = write_i32(slot, off, data["m_timeSeed"])

    for value in data["m_carColor"]:
        off = write_i32(slot, off, value)

    for value in data["m_carPurchased"]:
        off = write_bool8(slot, off, value)

    off = write_bool8(slot, off, data["m_isMPH"])

    for key in ("m_carDamage", "m_carFuel", "m_carNitro", "m_carRadar"):
        for value in data[key]:
            off = write_f32(slot, off, value)

    for key in ("m_carEngine", "m_carHandling", "m_carSteering", "m_carGearBox"):
        for value in data[key]:
            off = write_i32(slot, off, value)

    for value in data["m_missionFired"]:
        off = write_bool8(slot, off, value)

    for value in data["m_career"]:
        off = write_u8(slot, off, value)

    return bytes(slot)

def convert_3ds_blob_to_xml_root(blob: bytes, template_root: ET.Element) -> ET.Element:
    min_size = PLAYER_SLOTS * SLOT_SIZE
    if len(blob) < min_size:
        raise ParseError(f"3DS save is too small: {len(blob)} bytes")

    root = copy.deepcopy(template_root)
    players = root.find("m_players")
    if players is None or len(players) != PLAYER_SLOTS:
        raise ParseError("Template XML must contain exactly 3 player slots under <m_players>.")

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
        selected_id = struct.unpack_from("<I", blob, min_size)[0]
        set_text(root, "m_selectedID", selected_id)

    return root

def build_3ds_output_from_xml(tree_root: ET.Element, original_blob: bytes) -> bytes:
    min_size = PLAYER_SLOTS * SLOT_SIZE
    if len(original_blob) < min_size:
        raise ParseError(f"Original 3DS save is too small: {len(original_blob)} bytes")

    players = tree_root.find("m_players")
    if players is None or len(players) != PLAYER_SLOTS:
        raise ParseError("Edited XML must contain exactly 3 player slots under <m_players>.")

    out = bytearray(original_blob)

    for slot_index, player_elem in enumerate(list(players)):
        start = slot_index * SLOT_SIZE
        end = start + SLOT_SIZE
        out[start:end] = build_3ds_slot_from_xml(bytes(out[start:end]), player_elem)

    selected_elem = tree_root.find("m_selectedID")
    if selected_elem is not None and len(out) >= min_size + 4:
        selected_id = int((selected_elem.text or "0").strip() or "0")
        struct.pack_into("<I", out, min_size, selected_id)

    return bytes(out)

class XmlSaveEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("70s Undervolt - An 80s Overdrive Save Editor")
        self.root.geometry("1300x800")
        self.root.minsize(980, 620)

        self.file_path = None
        self.tree_root = None
        self.item_to_element = {}
        self.use_xor_on_save = True
        self.loaded_format_name = None
        self.loaded_mode = None
        self.original_3ds_blob = None
        self.template_source_path = None

        self.colors = {
            "bg_top": "#14001f",
            "bg_mid": "#4a0d67",
            "bg_bottom": "#0b2a6f",
            "panel": "#0b1020",
            "panel_2": "#101735",
            "text": "#e8f7ff",
            "cyan": "#00f0ff",
            "pink": "#ff3cac",
            "gold": "#ffbf4d",
            "entry_bg": "#09101e",
            "list_bg": "#0a1022",
        }

        self.configure_styles()
        self.build_ui()

        self.root.bind("<Configure>", self.on_root_resize)
        self.root.after(25, self.redraw_background)
        self.root.after(150, self.show_startup_notification)
    
    def show_startup_notification(self):
        messagebox.showinfo(
            "70s Undervolt",
            "70s Undervolt has Started Successfully.\n\nVersion: v1.0.0\nDeveloper: Cracko298"
        )

    def configure_styles(self):
        self.root.configure(bg=self.colors["panel"])

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "Synth.Treeview",
            background=self.colors["list_bg"],
            foreground=self.colors["cyan"],
            fieldbackground=self.colors["list_bg"],
            borderwidth=1,
            rowheight=24,
            font=("Consolas", 10),
        )
        style.map(
            "Synth.Treeview",
            background=[("selected", self.colors["pink"])],
            foreground=[("selected", "#000000")],
        )
        style.configure(
            "Synth.Treeview.Heading",
            background=self.colors["panel_2"],
            foreground=self.colors["gold"],
            relief="flat",
            font=("Consolas", 10, "bold"),
        )
        style.configure(
            "Vertical.TScrollbar",
            troughcolor=self.colors["panel"],
            background=self.colors["pink"],
            arrowcolor=self.colors["cyan"],
            bordercolor=self.colors["panel"],
            lightcolor=self.colors["panel_2"],
            darkcolor=self.colors["panel_2"],
        )
        style.configure(
            "Horizontal.TScrollbar",
            troughcolor=self.colors["panel"],
            background=self.colors["pink"],
            arrowcolor=self.colors["cyan"],
            bordercolor=self.colors["panel"],
            lightcolor=self.colors["panel_2"],
            darkcolor=self.colors["panel_2"],
        )
        style.configure(
            "TPanedwindow",
            background=self.colors["panel"],
            sashthickness=6,
        )

    def style_frame(self, parent, **kwargs):
        defaults = {"bg": self.colors["panel"], "highlightthickness": 1, "highlightbackground": self.colors["cyan"]}
        defaults.update(kwargs)
        return tk.Frame(parent, **defaults)

    def style_label(self, parent, text="", fg=None, bg=None, font=None, **kwargs):
        return tk.Label(
            parent,
            text=text,
            fg=fg or self.colors["text"],
            bg=bg or self.colors["panel"],
            font=font or ("Consolas", 10),
            **kwargs,
        )

    def neon_button(self, parent, text, command, accent=None):
        accent = accent or self.colors["pink"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.colors["panel_2"],
            fg=accent,
            activebackground=accent,
            activeforeground="#000000",
            relief="flat",
            bd=0,
            padx=12,
            pady=7,
            highlightthickness=1,
            highlightbackground=accent,
            font=("Consolas", 10, "bold"),
            cursor="hand2",
        )

    def style_entry(self, parent, textvariable=None, width=None):
        return tk.Entry(
            parent,
            textvariable=textvariable,
            width=width,
            bg=self.colors["entry_bg"],
            fg=self.colors["cyan"],
            insertbackground=self.colors["pink"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.colors["pink"],
            font=("Consolas", 10),
        )

    def style_text(self, parent, **kwargs):
        return tk.Text(
            parent,
            bg=self.colors["entry_bg"],
            fg=self.colors["cyan"],
            insertbackground=self.colors["pink"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.colors["pink"],
            font=("Consolas", 10),
            **kwargs,
        )

    def build_ui(self):
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.outer_frame = tk.Frame(self.canvas, bg="#000000", highlightthickness=0)
        self.canvas_window = self.canvas.create_window(0, 0, anchor="nw", window=self.outer_frame)
        self.version_text = self.canvas.create_text(
            10,
            self.root.winfo_height() - 10,
            text="v1.0.0",
            fill="pink",
            font=("Consolas", 8),
            anchor="sw"
        )

        self.credits_text = self.canvas.create_text( # this no workies, idk why
            self.root.winfo_width() - 10,
            self.root.winfo_height() - 10,
            text="Made by Cracko298",
            fill="pink",
            font=("Consolas", 8),
            anchor="se"
        )

        self.canvas.tag_raise(self.version_text)
        self.canvas.tag_raise(self.credits_text)
        self.top_bar = self.style_frame(self.outer_frame, bg=self.colors["panel_2"])
        self.top_bar.pack(fill="x", padx=12, pady=12)

        self.neon_button(self.top_bar, "Open 80s Overdrive Save", self.open_file, self.colors["cyan"]).pack(side="left", padx=4, pady=4)
        self.neon_button(self.top_bar, "Save", self.save_file, self.colors["pink"]).pack(side="left", padx=4, pady=4)
        self.neon_button(self.top_bar, "Save As", self.save_file_as, self.colors["gold"]).pack(side="left", padx=4, pady=4)
        self.neon_button(self.top_bar, "Reload Tree", self.reload_tree, self.colors["cyan"]).pack(side="left", padx=4, pady=4)

        self.path_label = self.style_label(
            self.top_bar,
            text="[No SaveGame Loaded]",
            anchor="w",
            bg=self.colors["panel_2"],
            fg=self.colors["text"],
            font=("Consolas", 10, "bold"),
        )
        self.path_label.pack(side="left", fill="x", expand=True, padx=12)

        self.main_pane = tk.PanedWindow(
            self.outer_frame,
            sashrelief="raised",
            sashwidth=6,
            bg=self.colors["panel"],
            bd=0,
            relief="flat",
        )
        self.main_pane.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left_frame = self.style_frame(self.main_pane)
        self.main_pane.add(left_frame, width=450)

        tree_header = self.style_label(left_frame, text="XML Tree", fg=self.colors["gold"], font=("Consolas", 11, "bold"))
        tree_header.pack(anchor="w", padx=8, pady=(8, 2))

        tree_container = tk.Frame(left_frame, bg=self.colors["panel"])
        tree_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.xml_tree = ttk.Treeview(tree_container, style="Synth.Treeview")
        self.xml_tree.pack(fill="both", expand=True, side="left")

        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.xml_tree.yview)
        tree_scroll.pack(fill="y", side="right")
        self.xml_tree.configure(yscrollcommand=tree_scroll.set)
        self.xml_tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        right_frame = self.style_frame(self.main_pane)
        self.main_pane.add(right_frame)

        form_frame = self.style_frame(right_frame, bg=self.colors["panel_2"])
        form_frame.pack(fill="x", padx=8, pady=8)

        self.style_label(form_frame, text="Tag:", fg=self.colors["gold"], bg=self.colors["panel_2"], font=("Consolas", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.tag_var = tk.StringVar()
        self.tag_entry = self.style_entry(form_frame, textvariable=self.tag_var, width=50)
        self.tag_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=4)

        self.style_label(form_frame, text="Text:", fg=self.colors["gold"], bg=self.colors["panel_2"], font=("Consolas", 10, "bold")).grid(row=1, column=0, sticky="nw")
        self.text_box = self.style_text(form_frame, height=8, wrap="word")
        self.text_box.grid(row=1, column=1, sticky="ew", padx=5, pady=4)

        form_frame.grid_columnconfigure(1, weight=1)

        attr_frame = tk.LabelFrame(
            right_frame,
            text="Attributes",
            bg=self.colors["panel_2"],
            fg=self.colors["gold"],
            font=("Consolas", 10, "bold"),
            relief="flat",
            bd=1,
            highlightthickness=1,
            highlightbackground=self.colors["pink"],
        )
        attr_frame.pack(fill="both", expand=False, padx=8, pady=8)

        self.attr_list = tk.Listbox(
            attr_frame,
            height=10,
            bg=self.colors["list_bg"],
            fg=self.colors["cyan"],
            selectbackground=self.colors["pink"],
            selectforeground="#000000",
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.colors["cyan"],
            font=("Consolas", 10),
        )
        self.attr_list.pack(fill="both", expand=True, side="left", padx=(6, 0), pady=6)
        self.attr_list.bind("<<ListboxSelect>>", self.on_attr_select)

        attr_scroll = ttk.Scrollbar(attr_frame, orient="vertical", command=self.attr_list.yview)
        attr_scroll.pack(fill="y", side="left", pady=6)
        self.attr_list.configure(yscrollcommand=attr_scroll.set)

        attr_edit_frame = tk.Frame(attr_frame, bg=self.colors["panel_2"])
        attr_edit_frame.pack(fill="both", expand=True, side="left", padx=10, pady=6)

        self.style_label(attr_edit_frame, text="Name:", fg=self.colors["gold"], bg=self.colors["panel_2"], font=("Consolas", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.attr_name_var = tk.StringVar()
        self.attr_name_entry = self.style_entry(attr_edit_frame, textvariable=self.attr_name_var)
        self.attr_name_entry.grid(row=0, column=1, sticky="ew", pady=3)

        self.style_label(attr_edit_frame, text="Value:", fg=self.colors["gold"], bg=self.colors["panel_2"], font=("Consolas", 10, "bold")).grid(row=1, column=0, sticky="w")
        self.attr_value_var = tk.StringVar()
        self.attr_value_entry = self.style_entry(attr_edit_frame, textvariable=self.attr_value_var)
        self.attr_value_entry.grid(row=1, column=1, sticky="ew", pady=3)

        self.neon_button(attr_edit_frame, "Update Attribute", self.update_attribute, self.colors["cyan"]).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=4
        )
        self.neon_button(attr_edit_frame, "Add Attribute", self.add_attribute, self.colors["gold"]).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=4
        )
        self.neon_button(attr_edit_frame, "Delete Attribute", self.delete_attribute, self.colors["pink"]).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=4
        )

        attr_edit_frame.grid_columnconfigure(1, weight=1)

        bottom_frame = self.style_frame(right_frame, bg=self.colors["panel_2"])
        bottom_frame.pack(fill="x", padx=8, pady=8)

        self.neon_button(
            bottom_frame,
            "Apply Changes To Selected Element",
            self.apply_element_changes,
            self.colors["pink"],
        ).pack(fill="x", padx=6, pady=6)

    def on_root_resize(self, event=None):
        if event is None or event.widget == self.root:
            w = self.root.winfo_width()
            h = self.root.winfo_height()

            self.canvas.itemconfigure(self.canvas_window, width=self.root.winfo_width(), height=self.root.winfo_height())
            self.redraw_background()

            self.canvas.coords(self.version_text, 10, h - 10)
            self.canvas.coords(self.credits_text, w - 10, h - 10)

    def hex_to_rgb(self, color):
        color = color.lstrip("#")
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))

    def rgb_to_hex(self, rgb):
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    def blend(self, c1, c2, t):
        r1, g1, b1 = self.hex_to_rgb(c1)
        r2, g2, b2 = self.hex_to_rgb(c2)
        return self.rgb_to_hex((
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t),
        ))

    def redraw_background(self):
        if not hasattr(self, "canvas"):
            return

        self.canvas.delete("bg")
        width = max(self.root.winfo_width(), 1)
        height = max(self.root.winfo_height(), 1)

        top = self.colors["bg_top"]
        mid = self.colors["bg_mid"]
        bottom = self.colors["bg_bottom"]

        split = max(height // 2, 1)
        for y in range(height):
            if y <= split:
                t = y / split
                color = self.blend(top, mid, t)
            else:
                t = (y - split) / max(height - split, 1)
                color = self.blend(mid, bottom, t)
            self.canvas.create_line(0, y, width, y, fill=color, tags="bg")

        glow_y = int(height * 0.66)
        for i in range(28):
            alpha_mix = i / 28.0
            color = self.blend(self.colors["pink"], self.colors["gold"], alpha_mix)
            self.canvas.create_line(0, glow_y + i, width, glow_y + i, fill=color, tags="bg")

        for y in range(0, height, 4):
            self.canvas.create_line(0, y, width, y, fill="#0a0010", tags="bg", stipple="gray25")

        self.canvas.tag_lower("bg")
        self.canvas.tag_raise(self.canvas_window)
        self.canvas.tag_raise(self.version_text)
        self.canvas.tag_raise(self.credits_text)

    def try_load_save_variants(self, raw_data: bytes):
        attempts = [
            ("PC/Steam/Xbox", True, xor_data(raw_data, KEY)),
            ("Nintendo Switch", False, raw_data),
        ]

        errors = []
        not_80s_error = None

        for label, use_xor, candidate_data in attempts:
            try:
                xml_text = try_decode_xml(candidate_data)
                parsed_root = validate_80s_overdrive_save(xml_text)
                return parsed_root, use_xor, label
            except ValueError as e:
                error_text = f"{label}: {e}"
                errors.append(error_text)
                if REQUIRED_TAG in str(e):
                    not_80s_error = error_text
            except Exception as e:
                errors.append(f"{label}: {e}")

        if not_80s_error is not None:
            raise ValueError(not_80s_error)

        raise ValueError(
            "Failed to load save. Not a Valid 80s Overdrive SaveGame "
            "Did not produce or load.\n\n" + "\n".join(errors)
        )

    def try_load_template_save(self, path: str) -> ET.Element:
        with open(path, "rb") as f:
            template_raw = f.read()

        template_root, _, _ = self.try_load_save_variants(template_raw)
        players = template_root.find("m_players")
        if players is None or len(players) != PLAYER_SLOTS:
            raise ParseError("Template save must contain exactly 3 player slots under <m_players>.")
        return template_root

    def try_load_3ds_save(self, raw_data: bytes):
        template_path = filedialog.askopenfilename(
            title="Select PC/Switch/Xbox save template for 3DS conversion",
            filetypes=[("Template File(s)", "*.template"), ("All files", "*.*")]
        )
        if not template_path:
            raise ValueError("3DS SaveGame Loading cancelled because no Template was Found.")

        template_root = self.try_load_template_save(template_path)
        converted_root = convert_3ds_blob_to_xml_root(raw_data, template_root)
        return converted_root, template_path

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open Save",
            filetypes=[("Save files", "*.sav"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            with open(path, "rb") as f:
                raw_data = f.read()

            self.template_source_path = None
            self.original_3ds_blob = None

            try:
                self.tree_root, self.use_xor_on_save, self.loaded_format_name = self.try_load_save_variants(raw_data)
                self.loaded_mode = "xml"
                self.file_path = path
                suffix = " [PC/Steam/Xbox SaveGame]" if self.use_xor_on_save else " [Nintendo Switch SaveGame]"
                self.path_label.config(text=path + suffix)
                self.reload_tree()
                messagebox.showinfo(
                    "Success",
                    f"Loaded 80s Overdrive save successfully.\nDetected format: {self.loaded_format_name}."
                )
                return
            except Exception:
                pass

            self.tree_root, self.template_source_path = self.try_load_3ds_save(raw_data)
            self.loaded_mode = "3ds"
            self.loaded_format_name = "Nintendo 3DS"
            self.use_xor_on_save = False
            self.original_3ds_blob = raw_data
            self.file_path = path
            template_name = Path(self.template_source_path).name
            self.path_label.config(text=path + f" [Nintendo 3DS SaveGame]")
            self.reload_tree()
            messagebox.showinfo(
                "Success",
                "Loaded 80s Overdrive 3DS SaveGame Successfully.\n"
                f"Template used: {self.template_source_path}\n\n"
                "Editing and saving will write the supported converted fields back into the 3DS Binary SaveGame."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open/validate/parse file:\n{e}")

    def reload_tree(self):
        self.xml_tree.delete(*self.xml_tree.get_children())
        self.item_to_element.clear()

        if self.tree_root is None:
            return

        self.add_tree_node("", self.tree_root)

    def add_tree_node(self, parent_item, element):
        text_preview = (element.text or "").strip()
        if len(text_preview) > 30:
            text_preview = text_preview[:30] + "..."

        label = element.tag
        if text_preview:
            label += f" = {text_preview}"

        item_id = self.xml_tree.insert(parent_item, "end", text=label, open=True)
        self.item_to_element[item_id] = element

        for child in element:
            self.add_tree_node(item_id, child)

    def get_selected_element(self):
        selected = self.xml_tree.selection()
        if not selected:
            return None
        return self.item_to_element.get(selected[0])

    def on_tree_select(self, event=None):
        element = self.get_selected_element()
        if element is None:
            return

        self.tag_var.set(element.tag)
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", element.text if element.text is not None else "")

        self.refresh_attr_list(element)

    def refresh_attr_list(self, element):
        self.attr_list.delete(0, "end")
        for k, v in element.attrib.items():
            self.attr_list.insert("end", f"{k} = {v}")
        self.attr_name_var.set("")
        self.attr_value_var.set("")

    def on_attr_select(self, event=None):
        element = self.get_selected_element()
        if element is None:
            return

        selection = self.attr_list.curselection()
        if not selection:
            return

        index = selection[0]
        key = list(element.attrib.keys())[index]
        value = element.attrib[key]

        self.attr_name_var.set(key)
        self.attr_value_var.set(value)

    def apply_element_changes(self):
        element = self.get_selected_element()
        if element is None:
            messagebox.showwarning("No Selection", "Select an element first.")
            return

        new_tag = self.tag_var.get().strip()
        if not new_tag:
            messagebox.showwarning("Invalid Tag", "Tag name cannot be empty.")
            return

        element.tag = new_tag
        element.text = self.text_box.get("1.0", "end-1c")

        self.reload_tree()
        messagebox.showinfo("Updated", "Element changes applied.")

    def update_attribute(self):
        element = self.get_selected_element()
        if element is None:
            messagebox.showwarning("No Selection", "Select an element first.")
            return

        name = self.attr_name_var.get().strip()
        value = self.attr_value_var.get()

        if not name:
            messagebox.showwarning("Invalid Attribute", "Attribute name cannot be empty.")
            return

        element.attrib[name] = value
        self.refresh_attr_list(element)

    def add_attribute(self):
        self.update_attribute()

    def delete_attribute(self):
        element = self.get_selected_element()
        if element is None:
            messagebox.showwarning("No Selection", "Select an element first.")
            return

        name = self.attr_name_var.get().strip()
        if not name:
            messagebox.showwarning("Invalid Attribute", "Select or enter an attribute name.")
            return

        if name in element.attrib:
            del element.attrib[name]
            self.refresh_attr_list(element)
        else:
            messagebox.showwarning("Missing Attribute", f"Attribute '{name}' not found.")

    def build_output_bytes(self) -> bytes:
        if self.tree_root is None:
            raise ValueError("No SaveGame loaded.")

        xml_text = pretty_xml(self.tree_root)

        if REQUIRED_TAG not in xml_text:
            raise ValueError(
                f"Refusing to save because required tag '{REQUIRED_TAG}' was not found."
            )

        if self.loaded_mode == "3ds":
            if self.original_3ds_blob is None:
                raise ValueError("Original 3DS save data is missing.")
            return build_3ds_output_from_xml(self.tree_root, self.original_3ds_blob)

        xml_bytes = xml_text.encode("utf-8")
        if self.use_xor_on_save:
            return xor_data(xml_bytes, KEY)
        return xml_bytes

    def save_file(self):
        if self.file_path is None:
            self.save_file_as()
            return

        try:
            output_data = self.build_output_bytes()
            with open(self.file_path, "wb") as f:
                f.write(output_data)
            if self.loaded_mode == "3ds":
                messagebox.showinfo("Saved", f"Saved Nintendo 3DS binary SaveGame back to:\n{self.file_path}")
            else:
                save_mode = "PC/Steam/Xbox" if self.use_xor_on_save else "Nintendo Switch"
                messagebox.showinfo("Saved", f"Saved {save_mode} SaveGame back to:\n{self.file_path}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save SaveGame:\n{e}")

    def save_file_as(self):
        if self.tree_root is None:
            messagebox.showwarning("No File", "No SaveGame file is Loaded.")
            return

        path = filedialog.asksaveasfilename(
            title="Save File As",
            defaultextension=".sav",
            filetypes=[("Save Files", "*.sav"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            output_data = self.build_output_bytes()
            with open(path, "wb") as f:
                f.write(output_data)
            self.file_path = path
            if self.loaded_mode == "3ds":
                self.path_label.config(text=path + " [Nintendo 3DS SaveGame]")
                messagebox.showinfo("Saved", f"Saved Nintendo 3DS Binary SaveGame to:\n{path}")
            else:
                suffix = " [PC/Steam/Xbox SaveGame]" if self.use_xor_on_save else " [Nintendo Switch SaveGame]"
                self.path_label.config(text=path + suffix)
                save_mode = "PC/Steam/Xbox" if self.use_xor_on_save else "Nintendo Switch"
                messagebox.showinfo("Saved", f"Saved {save_mode} SaveGame to:\n{path}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save SaveGame:\n{e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = XmlSaveEditor(root)
    root.mainloop()
