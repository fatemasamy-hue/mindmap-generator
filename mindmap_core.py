"""
Core logic for the Mind Map Generator app.
Handles the node data model, CSV / Google Sheet parsing, LaTeX escaping,
and the render -> xelatex -> PNG pipeline.
Kept separate from app.py so the UI stays readable.
"""

import csv
import io
import os
import re
import shutil
import subprocess
import uuid

from jinja2 import Environment, FileSystemLoader

CSV_HEADER = [
    "Level1", "Level2", "Level3", "BoxColor", 
    "Icon", "ChartShape", "Lang", "ChildPath", "CustomSettings",
]

# Nagwa Fill Colors (Level 2 user selections)
# Level 3 automatically takes the "2" variant of whatever is selected here.
COLOR_PRESETS = [
    "", "Pink1", "Peach1", "Olive1", "Sage1", "Blue1", "Purple1", "Rose1"
]

BUNDLED_ARABIC_FONT_FILE = "arial.ttf"
BUNDLED_ARABIC_FONT_LABEL = "Arial"

# Current defaults baked into template.tex for each level's spacing/sizing.
DEFAULT_LEVEL_STYLES = {
    "Level 1": {
        "level distance": "7cm",
        "sibling angle": "<< 360 // n if n > 0 else 360 >>",
        "text width": "3cm",
        "font": "\\fontsize{13.5pt}{16.2pt}\\selectfont\\bfseries",
        "inner sep": "10pt",
        "minimum size": "2.25cm",
    },
    "Level 2": {
        "level distance": "6cm",
        "sibling angle": "45",
        "text width": "3.5cm",
        "font": "\\fontsize{12pt}{14.4pt}\\selectfont\\bfseries",
        "inner sep": "10pt",
        "minimum size": "1.75cm",
    },
    "Level 3": {
        "level distance": "6cm",
        "sibling angle": "90",
        "text width": "2.5cm",
        "font": "\\fontsize{12pt}{14.4pt}\\selectfont\\bfseries",
        "inner sep": "10pt",
        "minimum size": "1.15cm",
    },
}

CUSTOM_SETTINGS_REFERENCE = {
    "sibling angle=80": {
        "applies_to": "MOTHER (Parent)",
        "type": "Spacing Parameters",
        "description": "Increases or decreases the fan spread of the children attached to this mother.",
    },
    "level distance=8cm": {
        "applies_to": "MOTHER (Parent)",
        "type": "Spacing Parameters",
        "description": "Pushes all children attached to this mother further out from her center.",
    },
    "text width=5cm": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Appearance Parameters",
        "description": "Makes the invisible text box wider, forcing long sentences to stay on fewer lines.",
    },
    "inner sep=15pt": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Appearance Parameters",
        "description": "Increases the padding between the text and the outer edge of the bubble, making the bubble physically fatter.",
    },
    "font=\\fontsize{12pt}{14.4pt}\\selectfont": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Appearance Parameters",
        "description": "Changes the exact font size using numerical point values. The first value is the text size, the second is line spacing (use 1.2x font size). Add \\bfseries at the end for bold.",
    },
    "rectangle, rounded corners=3pt": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Appearance Parameters",
        "description": "Forces this single bubble to be a box, even if the rest of the chart is made of circles.",
    },
    "circle": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Appearance Parameters",
        "description": "Forces this single bubble to be a circle, even if the rest of the chart is made of rectangles.",
    },
    "fill=Fushia1": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Appearance Parameters",
        "description": "Overrides the default or inherited box color. Use any exact Nagwa fill color name.",
    },
    "text=Red": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Appearance Parameters",
        "description": "Overrides the default black text color. Use any exact Nagwa text color name (or white).",
    },
    "grow=45": {
        "applies_to": "BUBBLE ITSELF (Self)",
        "type": "Direction Parameters",
        "description": "Forces this specific branch to point exactly at 45 degrees on the compass, ignoring the chart's automatic symmetry.",
    },
}

COMPASS_DIRECTIONS = [
    (0, "Straight to the Right (3:00) — Note: Flips to Left (9:00) in Arabic mode"),
    (45, "Top Right diagonal (1:00) — Flips to Top Left in Arabic mode"),
    (90, "Straight Up (12:00)"),
    (135, "Top Left diagonal (10:00) — Flips to Top Right in Arabic mode"),
    (180, "Straight to the Left (9:00) — Flips to Right (3:00) in Arabic mode"),
    (225, "(or -135°) Bottom Left diagonal (7:00) — Flips to Bottom Right in Arabic mode"),
    (270, "(or -90°) Straight Down (6:00)"),
    (315, "(or -45°) Bottom Right diagonal (4:00) — Flips to Bottom Left in Arabic mode"),
]

# --------------------------------------------------------------------------
# Data model helpers
# --------------------------------------------------------------------------

def new_node(text="", box_color="", text_color="black", icon="", custom=""):
    return {
        "id": uuid.uuid4().hex,
        "text": text,
        "box_color": box_color,
        "text_color": text_color,
        "icon": icon,
        "custom": custom,
        "children": [],
    }


def default_tree():
    root = new_node(text="Central Topic", box_color="", text_color="black")
    return root


# --------------------------------------------------------------------------
# LaTeX escaping & Node Building
# --------------------------------------------------------------------------

def escape_latex(text):
    if not text:
        return text
    text = text.replace("\\\\", "[NL]").replace("\\", "[NL]").replace("\n", "[NL]")
    for char in ["#", "&", "%", "$", "_"]:
        text = text.replace(char, "\\" + char)
    text = text.replace("[NL]", "\\\\")
    return text


def compute_fill(box_color):
    box_color_clean = (box_color or "").strip()
    if not box_color_clean:
        return "RootGrey"
        
    if box_color_clean.lower() == "white":
        return "white"
        
    # Return the exact color name (no opacity shades needed anymore)
    return box_color_clean


def build_render_node(node, level, parent_color=""):
    raw_color = node.get("box_color", "").strip()
    
    # --- COLOR CASCADE LOGIC ---
    if level == 1 and not raw_color:
        raw_color = "RootGrey"
    elif level == 2 and not raw_color:
        raw_color = "Blue1" # Fallback if Level 2 is left blank
    elif level >= 3:
        # Automatically map "1" colors to "2" colors (e.g. Pink1 -> Pink2)
        if parent_color and parent_color.endswith("1"):
            raw_color = parent_color[:-1] + "2"
        else:
            raw_color = parent_color

    return {
        "text": escape_latex(node.get("text", "")),
        "fill": compute_fill(raw_color),
        "text_color": node.get("text_color") or "black",
        "icon": node.get("icon", ""),
        "custom": node.get("custom", ""),
        "children": [build_render_node(c, level + 1, raw_color) for c in node.get("children", [])],
    }


# --------------------------------------------------------------------------
# CSV / Google Sheet import
# --------------------------------------------------------------------------

EXPECTED_HEADERS = {
    "level1": "l1", "level2": "l2", "level3": "l3",
    "boxcolor": "box_color", "icon": "icon",
    "chartshape": "chart_shape", "lang": "lang", "language": "lang",
    "childpath": "child_path", "customsettings": "custom",
}


def _normalize_header(cell):
    return re.sub(r"[^a-z0-9]", "", (cell or "").strip().lower())


def _find_header_row(rows):
    for idx, row in enumerate(rows):
        col_map = {}
        for col_idx, cell in enumerate(row):
            field = EXPECTED_HEADERS.get(_normalize_header(cell))
            if field:
                col_map[field] = col_idx
        if "l1" in col_map:
            return idx, col_map
    return None, None


def parse_csv_text_to_tree(csv_text):
    delimiter = "\t" if "\t" in csv_text.splitlines()[0] else ","
    reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)

    rows = list(reader)
    if not rows:
        return None, {}, "The sheet/file appears to be empty."

    header_idx, col_map = _find_header_row(rows)
    if col_map is not None:
        data_rows = rows[header_idx + 1:]
    else:
        col_map = {
            "l1": 0, "l2": 1, "l3": 2, "box_color": 3,
            "icon": 4, "chart_shape": 5, "lang": 6, "child_path": 7, "custom": 8,
        }
        data_rows = rows[1:]

    def get(row, field):
        idx = col_map.get(field)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    root = None
    current_l2 = None
    settings = {"chart_shape": "circle", "lang": "en", "child_path": "gradient"}

    for row in data_rows:
        if not any(c.strip() for c in row):
            continue

        l1, l2, l3 = get(row, "l1"), get(row, "l2"), get(row, "l3")
        box_color, icon, custom = (
            get(row, "box_color"), get(row, "icon"), get(row, "custom"),
        )
        chart_shape, lang, child_path = get(row, "chart_shape"), get(row, "lang"), get(row, "child_path")

        if l1:
            level = 1
        elif l2:
            level = 2
        elif l3:
            level = 3
        else:
            continue

        node = new_node(
            text=l1 or l2 or l3,
            box_color=box_color,
            text_color="black", # Implicitly forced to black
            icon=icon,
            custom=custom,
        )

        if level == 1:
            root = node
            settings["chart_shape"] = "rectangle" if "rect" in chart_shape.lower() else "circle"
            settings["lang"] = "ar" if "ar" in lang.lower() else "en"
            settings["child_path"] = "grey" if "grey" in child_path.lower() or "gray" in child_path.lower() else "gradient"
            current_l2 = None
        elif level == 2:
            if root is None:
                return None, {}, "Found a Level 2 entry before any Level 1 (root) row."
            root["children"].append(node)
            current_l2 = node
        elif level == 3:
            if current_l2 is None:
                return None, {}, "Found a Level 3 entry with no preceding Level 2 parent."
            current_l2["children"].append(node)

    if root is None:
        return None, {}, "No Level1 (root) row found. Exactly one row needs a value in the Level1 column."

    return root, settings, None


def tree_to_csv_text(root, chart_shape, lang, child_path):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    writer.writerow([
        root.get("text", ""), "", "",
        root.get("box_color", ""), root.get("icon", ""),
        chart_shape, lang, child_path, root.get("custom", ""),
    ])
    for child in root.get("children", []):
        writer.writerow([
            "", child.get("text", ""), "",
            child.get("box_color", ""), child.get("icon", ""),
            "", "", "", child.get("custom", ""),
        ])
        for grandchild in child.get("children", []):
            writer.writerow([
                "", "", grandchild.get("text", ""),
                grandchild.get("box_color", ""), grandchild.get("icon", ""),
                "", "", "", grandchild.get("custom", ""),
            ])
    return buf.getvalue()


def sample_csv_template_text():
    sample_root = new_node(text="Central Topic", box_color="Blue1", icon="lightbulb")
    c1 = new_node(text="Branch One", box_color="Sage1", icon="star")
    c1["children"].append(new_node(text="Detail A", box_color="Sage1"))
    c1["children"].append(new_node(text="Detail B", box_color="Sage1"))
    c2 = new_node(text="Branch Two", box_color="Peach1", icon="cog")
    sample_root["children"] = [c1, c2]
    return tree_to_csv_text(sample_root, "circle", "en", "gradient")


def google_sheet_url_to_csv_url(sheet_url):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not match:
        return None, "Couldn't find a sheet ID in that URL. Paste the full URL from your browser's address bar."
    sheet_id = match.group(1)
    gid_match = re.search(r"[?&#]gid=(\d+)", sheet_url)
    gid = gid_match.group(1) if gid_match else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}", None


# --------------------------------------------------------------------------
# Render + compile pipeline
# --------------------------------------------------------------------------

def render_tex(root, chart_shape, lang, child_path, arabic_font, template_dir, template_name="template.tex"):
    render_root = build_render_node(root, level=1)
    env = Environment(
        loader=FileSystemLoader(template_dir),
        block_start_string="<%",
        block_end_string="%>",
        variable_start_string="<<",
        variable_end_string=">>",
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_name)
    
    font_dir = template_dir.replace("\\", "/")
    if not font_dir.endswith("/"):
        font_dir += "/"
        
    return template.render(
        root=render_root,
        shape=chart_shape,
        lang=lang,
        child_path=child_path,
        arabic_font=arabic_font,
        bundled_font_label=BUNDLED_ARABIC_FONT_LABEL,
        bundled_font_file=BUNDLED_ARABIC_FONT_FILE,
        font_dir=font_dir,
    )


def fit_image_to_size(png_path, target_width_pt, target_height_pt, dpi):
    from PIL import Image

    px_per_pt = dpi / 72.0
    target_w = max(1, round(target_width_pt * px_per_pt))
    target_h = max(1, round(target_height_pt * px_per_pt))

    img = Image.open(png_path).convert("RGBA")
    scale = min(target_w / img.width, target_h / img.height, 1.0)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    offset = ((target_w - new_w) // 2, (target_h - new_h) // 2)
    canvas.paste(resized, offset, resized)
    canvas.save(png_path, "PNG")
    return png_path


def check_dependencies():
    problems = []
    if shutil.which("xelatex") is None:
        problems.append(
            "`xelatex` was not found on this server. Install a TeX distribution "
            "(e.g. `texlive-xetex`) — see packages.txt / README for the exact packages."
        )
    if shutil.which("pdftoppm") is None:
        problems.append(
            "`poppler` was not found on this server (needed to convert PDF to PNG). "
            "Install `poppler-utils`."
        )
    return problems


def compile_mindmap(root, chart_shape, lang, child_path, arabic_font, work_dir, template_dir,
                     fit_size_pt=None, dpi=300):
    os.makedirs(work_dir, exist_ok=True)
    tex_source = render_tex(root, chart_shape, lang, child_path, arabic_font, template_dir)

    tex_path = os.path.join(work_dir, "output_chart.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex_source)

    result = {"success": False, "tex_path": tex_path, "pdf_path": None, "png_path": None, "log": ""}

    deps = check_dependencies()
    if deps:
        result["log"] = "\n".join(deps)
        return result

    try:
        proc = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error", "output_chart.tex"],
            cwd=work_dir, capture_output=True, text=True, timeout=120,
        )
        
        stdout_text = proc.stdout if proc.stdout else ""
        stderr_text = proc.stderr if proc.stderr else ""
        result["log"] = stdout_text[-4000:] + "\n" + stderr_text[-2000:]
        
    except subprocess.TimeoutExpired:
        result["log"] = "xelatex timed out after 120 seconds."
        return result

    pdf_path = os.path.join(work_dir, "output_chart.pdf")
    if not os.path.exists(pdf_path):
        return result

    result["pdf_path"] = pdf_path

    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, dpi=dpi)
        png_path = os.path.join(work_dir, "output_chart.png")
        images[0].save(png_path, "PNG")
        if fit_size_pt:
            fit_image_to_size(png_path, fit_size_pt[0], fit_size_pt[1], dpi)
        result["png_path"] = png_path
        result["success"] = True
    except Exception as e:
        result["log"] += f"\n\nPDF was created but PNG conversion failed: {e}"

    return result
