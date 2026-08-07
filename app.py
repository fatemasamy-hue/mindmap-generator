import base64
import os
import tempfile

import requests
import streamlit as st

import mindmap_core as core

st.set_page_config(page_title="Mind Map Generator", page_icon="🧠", layout="wide")

TEMPLATE_DIR = os.path.dirname(os.path.abspath(__file__))

# Preview zoom only — never changes the fixed 752×492 pt canvas.
ZOOM_MIN = 0.25
ZOOM_MAX = 2.0  # at least 200% — preview scrolls when larger than the panel
ZOOM_STEP = 0.25
ZOOM_DEFAULT = 1.0

# --------------------------------------------------------------------------
if "tree" not in st.session_state:
    st.session_state.tree = core.default_tree()
if "chart_shape" not in st.session_state:
    st.session_state.chart_shape = "circle"
if "lang" not in st.session_state:
    st.session_state.lang = "en"
if "child_path" not in st.session_state:
    st.session_state.child_path = "gradient"
if "layout_mode" not in st.session_state:
    st.session_state.layout_mode = "radial"
if "edge_style" not in st.session_state:
    st.session_state.edge_style = "standard"
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "tree_version" not in st.session_state:
    st.session_state.tree_version = 0
if "preview_zoom" not in st.session_state:
    st.session_state.preview_zoom = ZOOM_DEFAULT
if "import_message" not in st.session_state:
    st.session_state.import_message = None


def reset_all():
    st.session_state.tree = core.default_tree()
    st.session_state.last_result = None
    st.session_state.tree_version += 1


def bump_version():
    st.session_state.tree_version += 1


# --------------------------------------------------------------------------
# Sidebar — global settings
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Global settings")
    st.caption("These apply to the whole mind map (they used to live in hidden CSV columns).")

    st.session_state.chart_shape = st.radio(
        "Node shape", ["circle", "rectangle"],
        index=["circle", "rectangle"].index(st.session_state.chart_shape),
        help="Circle uses TikZ's radial 'concept' style. Rectangle uses rounded boxes.",
    )
    st.session_state.lang = st.radio(
        "Language / direction", ["en", "ar"],
        format_func=lambda v: "English (left-to-right)" if v == "en" else "Arabic (right-to-left)",
        index=["en", "ar"].index(st.session_state.lang),
    )
    st.session_state.child_path = st.radio(
        "Branch coloring", ["gradient", "grey"],
        format_func=lambda v: "Gradient (branches inherit root color)" if v == "gradient" else "Grey (neutral branches)",
        index=["gradient", "grey"].index(st.session_state.child_path),
    )
    st.session_state.layout_mode = st.radio(
        "Layout structure", ["radial", "vertical", "horizontal"],
        format_func=lambda v: {"radial": "Radial Mindmap", "vertical": "Vertical Tree", "horizontal": "Horizontal Tree"}[v],
        index=["radial", "vertical", "horizontal"].index(st.session_state.layout_mode),
        help="Change the overall structure from a circular map to a top-down or left-to-right tree."
    )
    st.session_state.edge_style = st.radio(
        "Connection lines", ["standard", "orthogonal", "ortho_arrows"],
        format_func=lambda v: {"standard": "Standard (Curved)", "orthogonal": "Orthogonal (Right Angles)", "ortho_arrows": "Orthogonal with Arrows"}[v],
        index=["standard", "orthogonal", "ortho_arrows"].index(st.session_state.edge_style),
        help="Only applies to Vertical and Horizontal tree layouts."
    )

    font_label = "Noto (Regular / Bold)" if st.session_state.lang == "ar" else "STIX2Text"
    st.caption(f"Font (auto): **{font_label}** — chosen from the selected language.")

    st.divider()
    st.subheader("🖼️ Canvas size")
    st.caption(
        f"Output is always **{core.CANVAS_WIDTH_PT}×{core.CANVAS_HEIGHT_PT} pt**. "
        "Use ➖ / ➕ in the preview panel to zoom the view only."
    )

    st.divider()
    if st.button("🗑️ Reset mind map", use_container_width=True):
        reset_all()
        st.rerun()

    with st.expander("ℹ️ About / how this works"):
        st.write(
            "Build your mind map using the Manual Builder, a linked Google Sheet, "
            "or an uploaded CSV — then generate the image. No LaTeX or CSV knowledge required."
        )

st.title("🧠 Mind Map Generator")
st.caption("Build a mind map, preview it, and export a print-ready PDF/PNG — no LaTeX or spreadsheet wrangling needed.")


# --------------------------------------------------------------------------
# Shared editing widgets
# --------------------------------------------------------------------------

def node_editor(node, key_prefix, level):
    """Renders the editable fields for one node. Mutates `node` in place."""
    node["text"] = st.text_input("Text", value=node["text"], key=f"{key_prefix}_text")
    
    # Only show the Box Color dropdown for Level 1 and Level 2
    if level in [1, 2]:
        current_color = node.get("box_color", "")
        if current_color not in core.COLOR_PRESETS:
            current_color = core.COLOR_PRESETS[0]
        node["box_color"] = st.selectbox(
            "Box color", core.COLOR_PRESETS, 
            index=core.COLOR_PRESETS.index(current_color), 
            key=f"{key_prefix}_box"
        )
    
    # Force text color to black implicitly without showing a UI field
    node["text_color"] = "black"

    node["icon"] = st.text_input(
        "Icon (optional)", 
        value=node["icon"], 
        key=f"{key_prefix}_icon",
        help="Paste the exact icon name from the Font Awesome 5 Free website."
    )

    with st.expander("Override the default"):
        st.caption(
            "Power-user only. Appended directly as extra TikZ node options. "
            "If applying multiple overrides, separate them with commas. "
            "See the 📖 Cheat Sheet tab for a list of common ones."
        )
        node["custom"] = st.text_input("Custom TikZ options", value=node["custom"], key=f"{key_prefix}_custom_latex")


def node_count_summary(root):
    l2 = len(root.get("children", []))
    l3 = sum(len(c.get("children", [])) for c in root.get("children", []))
    return l2, l3


# --------------------------------------------------------------------------
# Tab renderers
# --------------------------------------------------------------------------

def render_manual_builder():
    root = st.session_state.tree
    v = st.session_state.tree_version

    st.subheader("Root topic")
    with st.expander(f"Root: {root['text'] or '(untitled)'}", expanded=True):
        node_editor(root, f"v{v}_root_{root['id']}", level=1)

    st.subheader("Topics (Level 2)")
    if st.button("➕ Add topic", key="add_l2"):
        root["children"].append(core.new_node())
        st.rerun()

    for i, child in enumerate(list(root["children"])):
        with st.expander(f"Topic {i + 1}: {child['text'] or '(untitled)'}", expanded=False):
            node_editor(child, f"v{v}_l2_{child['id']}", level=2)

            st.markdown("**Sub-topics (Level 3)**")
            if st.button("➕ Add sub-topic", key=f"add_l3_{child['id']}"):
                child["children"].append(core.new_node())
                st.rerun()

            for j, grandchild in enumerate(list(child["children"])):
                with st.container(border=True):
                    st.caption(f"Sub-topic {j + 1}")
                    node_editor(grandchild, f"v{v}_l3_{grandchild['id']}", level=3)
                    
                    if st.button("➕ Add L4 detail", key=f"add_l4_{grandchild['id']}"):
                        grandchild["children"].append(core.new_node())
                        st.rerun()
                        
                    for k, ggrandchild in enumerate(list(grandchild["children"])):
                        with st.container(border=True):
                            st.caption(f"L4 Detail {k + 1}")
                            node_editor(ggrandchild, f"v{v}_l4_{ggrandchild['id']}", level=4)
                            
                            if st.button("➕ Add L5 detail", key=f"add_l5_{ggrandchild['id']}"):
                                ggrandchild["children"].append(core.new_node())
                                st.rerun()
                                
                            for m, gggrandchild in enumerate(list(ggrandchild["children"])):
                                with st.container(border=True):
                                    st.caption(f"L5 Detail {m + 1}")
                                    node_editor(gggrandchild, f"v{v}_l5_{gggrandchild['id']}", level=5)
                                    if st.button("🗑️ Remove L5", key=f"rm_l5_{gggrandchild['id']}"):
                                        ggrandchild["children"] = [c for c in ggrandchild["children"] if c["id"] != gggrandchild["id"]]
                                        st.rerun()
                                        
                            if st.button("🗑️ Remove L4", key=f"rm_l4_{ggrandchild['id']}"):
                                grandchild["children"] = [c for c in grandchild["children"] if c["id"] != ggrandchild["id"]]
                                st.rerun()

                    if st.button("🗑️ Remove sub-topic", key=f"rm_l3_{grandchild['id']}"):
                        child["children"] = [c for c in child["children"] if c["id"] != grandchild["id"]]
                        st.rerun()

            st.divider()
            if st.button("🗑️ Remove this topic (and its sub-topics)", key=f"rm_l2_{child['id']}"):
                root["children"] = [c for c in root["children"] if c["id"] != child["id"]]
                st.rerun()

    st.divider()
    st.subheader("Back up this mind map")
    st.caption("Download as CSV — reopen it later with Upload CSV, or paste it into a Google Sheet for the import option.")
    csv_text = core.tree_to_csv_text(root, st.session_state.chart_shape, st.session_state.lang, st.session_state.child_path)
    st.download_button("⬇️ Download as CSV", data=csv_text, file_name="mindmap_backup.csv", mime="text/csv")


def _apply_import(root_node, settings):
    st.session_state.tree = root_node
    st.session_state.chart_shape = settings["chart_shape"]
    st.session_state.lang = settings["lang"]
    st.session_state.child_path = settings["child_path"]
    bump_version()
    l2, l3 = node_count_summary(root_node)
    
    st.session_state.import_message = f"✅ Done! Successfully loaded \"{root_node['text']}\" — {l2} topic(s), {l3} sub-topic(s)."
    st.rerun()


def render_sheet_import():
    st.subheader("Import from a Google Sheet")
    st.write(
        "1. Structure your sheet with these columns: "
        f"`{', '.join(core.CSV_HEADER)}`.\n"
        "2. Share it as **Anyone with the link → Viewer**.\n"
        "3. Paste the link below."
    )
    st.download_button(
        "⬇️ Download a starter template (CSV, importable into Google Sheets)",
        data=core.sample_csv_template_text(),
        file_name="mindmap_template.csv",
        mime="text/csv",
        key="dl_sheet_template",
    )

    sheet_url = st.text_input("Google Sheet URL", placeholder="https://docs.google.com/spreadsheets/d/....")
    if st.button("📥 Load from Google Sheet", key="load_sheet_btn"):
        if not sheet_url.strip():
            st.warning("Paste a Google Sheet URL first.")
        else:
            csv_url, err = core.google_sheet_url_to_csv_url(sheet_url.strip())
            if err:
                st.error(err)
            else:
                try:
                    resp = requests.get(csv_url, timeout=15)
                    if resp.status_code == 200:
                        resp.encoding = 'utf-8'
                        with st.expander("🔍 Raw data received from the sheet (for troubleshooting)"):
                            st.text(resp.text[:2000])
                        root_node, settings, parse_err = core.parse_csv_text_to_tree(resp.text)
                        if parse_err:
                            st.error(f"Couldn't read that sheet: {parse_err}")
                        else:
                            _apply_import(root_node, settings)
                    else:
                        st.error(
                            f"Google Sheets returned status {resp.status_code}. "
                            "Make sure the sheet is shared as 'Anyone with the link can view'."
                        )
                except requests.RequestException as e:
                    st.error(f"Couldn't reach that sheet: {e}")


def render_csv_upload():
    st.subheader("Upload a CSV")
    st.caption(f"Columns expected: {', '.join(core.CSV_HEADER)}")
    uploaded = st.file_uploader("Choose a CSV file", type=["csv", "tsv", "txt"], key="csv_uploader")
    if uploaded is not None:
        text = uploaded.read().decode("utf-8")
        root_node, settings, parse_err = core.parse_csv_text_to_tree(text)
        if parse_err:
            st.error(f"Couldn't read that file: {parse_err}")
        else:
            if st.button("📥 Load this CSV into the builder", key="load_csv_btn"):
                _apply_import(root_node, settings)


def render_cheat_sheet():
    st.subheader("📖 Custom Settings Cheat Sheet")
    st.markdown(
        "**The \"Mother vs. Self\" Rule**\n\n"
        "- **Apply to the MOTHER (Parent):** If you want to change the distance or the fanning angle of the outer leaves, you must give the command to the Mother bubble. She is the one holding the invisible ruler that spaces out her children.\n"
        "- **Apply to the BUBBLE ITSELF (Self):** If you want to change how a bubble looks (its shape, size, text width) or the specific compass direction it shoots off into, you give the command to that bubble's own row in the CSV."
    )

    st.markdown("#### The Custom Parameter Cheat Sheet")
    st.caption("Here are the most useful commands you can paste into your 10th column, broken down by exactly where to put them in your CSV.")
    
    for setting, info in core.CUSTOM_SETTINGS_REFERENCE.items():
        c1, c2 = st.columns([1, 2])
        with c1:
            st.code(setting, language="latex")
            st.caption(f"Apply to: **{info['applies_to']}**")
        with c2:
            st.write(info["description"])
        st.divider()

    st.markdown("#### Current Defaults Per Level")
    st.caption("This is what's already applied — a custom override only needs to state what's *different*.")
    for level_name, props in core.DEFAULT_LEVEL_STYLES.items():
        with st.expander(level_name):
            for k, v in props.items():
                st.write(f"- **{k}:** {v}")

    st.markdown("#### Compass Directions (for `grow=` overrides)")
    st.caption("Use with the direction override on a bubble itself, e.g. `grow=45`.")
    for degrees, label in core.COMPASS_DIRECTIONS:
        st.write(f"- **{degrees}°** — {label}")


@st.dialog("Mind Map Preview", width="large")
def _fullscreen_dialog(png_path):
    st.image(png_path, use_container_width=True)


def _preview_with_safe_area(png_path, zoom):
    """Show the preview at the given visual zoom with a dashed 752×492 safe-area guide."""
    display_w = max(1, int(round(core.CANVAS_WIDTH_PT * zoom)))
    aspect = f"{core.CANVAS_WIDTH_PT} / {core.CANVAS_HEIGHT_PT}"

    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    st.caption(
        f"Safe area: {core.CANVAS_WIDTH_PT}×{core.CANVAS_HEIGHT_PT} pt "
        f"(dashed border) · preview zoom {zoom:.0%}"
    )
    st.markdown(
        f"""
        <div style="
            width: 100%;
            overflow: auto;
            line-height: 0;
        ">
          <div style="
            position: relative;
            width: {display_w}px;
            aspect-ratio: {aspect};
            box-sizing: border-box;
            border: 2px dashed #6b7280;
            background:
              linear-gradient(to right, rgba(107,114,128,0.15) 1px, transparent 1px) 0 0 / 25% 100%,
              linear-gradient(to bottom, rgba(107,114,128,0.15) 1px, transparent 1px) 0 0 / 100% 25%,
              #ffffff;
            box-shadow: inset 0 0 0 1px rgba(107,114,128,0.2);
          ">
            <img
              src="data:image/png;base64,{b64}"
              alt="Mind map preview"
              style="
                position: absolute;
                inset: 0;
                width: 100%;
                height: 100%;
                object-fit: contain;
                display: block;
              "
            />
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_preview_panel():
    st.subheader("🖼️ Preview & Export")

    deps_missing = core.check_dependencies()
    if deps_missing:
        st.warning(
            "This server is missing a dependency needed to compile mind maps:\n\n"
            + "\n".join(f"- {d}" for d in deps_missing)
        )

    if not st.session_state.tree["text"].strip():
        st.info("Give your root topic some text in the Manual Builder first.")

    z1, z2, z3 = st.columns([1, 1, 3])
    with z1:
        if st.button("➖", key="preview_zoom_out", use_container_width=True,
                     disabled=st.session_state.preview_zoom <= ZOOM_MIN):
            st.session_state.preview_zoom = max(ZOOM_MIN, round(st.session_state.preview_zoom - ZOOM_STEP, 2))
            st.rerun()
    with z2:
        if st.button("➕", key="preview_zoom_in", use_container_width=True,
                     disabled=st.session_state.preview_zoom >= ZOOM_MAX):
            st.session_state.preview_zoom = min(ZOOM_MAX, round(st.session_state.preview_zoom + ZOOM_STEP, 2))
            st.rerun()
    with z3:
        st.caption(
            f"Zoom {st.session_state.preview_zoom:.0%} · "
            f"canvas {core.CANVAS_WIDTH_PT}×{core.CANVAS_HEIGHT_PT} pt (fixed)"
        )

    if st.button("🚀 Generate mind map", type="primary", disabled=bool(deps_missing), key="generate_btn"):
        with st.spinner("Compiling LaTeX and rendering image…"):
            work_dir = tempfile.mkdtemp(prefix="mindmap_")
            result = core.compile_mindmap(
                st.session_state.tree,
                st.session_state.chart_shape,
                st.session_state.lang,
                st.session_state.child_path,
                st.session_state.layout_mode,
                st.session_state.edge_style,
                work_dir,
                TEMPLATE_DIR,
            )
            st.session_state.last_result = result

    result = st.session_state.last_result
    if result:
        if result["success"]:
            st.success("Done!")
            _preview_with_safe_area(result["png_path"], st.session_state.preview_zoom)

            if st.button("🔍 Fullscreen", key="fullscreen_btn"):
                _fullscreen_dialog(result["png_path"])

            c1, c2, c3 = st.columns(3)
            with c1:
                with open(result["png_path"], "rb") as f:
                    st.download_button("⬇️ PNG", f, file_name="mindmap.png", mime="image/png", key="dl_png")
            with c2:
                with open(result["pdf_path"], "rb") as f:
                    st.download_button("⬇️ PDF", f, file_name="mindmap.pdf", mime="application/pdf", key="dl_pdf")
            with c3:
                with open(result["tex_path"], "rb") as f:
                    st.download_button("⬇️ .tex", f, file_name="mindmap.tex", mime="text/plain", key="dl_tex")
        else:
            st.error("Compilation failed. See the log below for details.")
            with st.expander("Compile log", expanded=True):
                st.code(result["log"] or "(no log captured)")


# --------------------------------------------------------------------------
# Main layout
# --------------------------------------------------------------------------

if st.session_state.import_message:
    st.success(st.session_state.import_message)
    st.session_state.import_message = None

left, right = st.columns([1.15, 1])
with left:
    t_manual, t_sheet, t_csv, t_cheat = st.tabs(
        ["🖊️ Manual Builder", "📄 Google Sheet", "📤 Upload CSV", "📖 Cheat Sheet"]
    )
    with t_manual:
        render_manual_builder()
    with t_sheet:
        render_sheet_import()
    with t_csv:
        render_csv_upload()
    with t_cheat:
        render_cheat_sheet()
with right:
    with st.container(border=True):
        render_preview_panel()
