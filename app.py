"""Streamlit front end for the gas / power desk monitor.

Run locally with:  streamlit run app.py
The workflow follows a desk: generate, read, review, export. The note is a
visual document in which each chart appears at the point in the argument it
supports, and every drafted paragraph can be confirmed, edited or rejected
before the note leaves the desk.
"""

import json
from pathlib import Path

import streamlit as st

from energydesk.config import (
    DEFAULT_EFFICIENCY, DEFAULT_EMISSION_FACTOR, DEFAULT_VARIABLE_OPEX,
    Settings,
)
from energydesk.pipeline import DataError, DeskMonitor
from energydesk.reporting.citations import cited_html, cited_markdown
from energydesk.reporting.noteblocks import (
    CHART_FILES, Block, CONFIRMED, EDITED, PENDING, REJECTED, STATUS_LABELS,
    build_blocks, load_saved_note,
)
from energydesk.reporting.pdf import note_pdf
from energydesk.reporting.snapshot import snapshot_table

st.set_page_config(
    page_title="EU Gas / Power Desk Monitor",
    page_icon=":electric_plug:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; max-width: 1180px; }
      h1, h2, h3 { letter-spacing: -0.01em; }
      .note-title { font-size: 2.05rem; font-weight: 700; margin-bottom: 0; }
      .note-sub { color: #6b7280; font-size: 0.95rem; margin-top: 0.15rem; }
      .callout { padding: 0.85rem 1.05rem; border-radius: 8px;
                 border-left: 4px solid #1f3a68; background: #f1f3f7;
                 margin: 0.6rem 0 1.1rem 0; font-size: 0.98rem; }
      hr { margin: 1.4rem 0; }
      .note-header { border-bottom: 2px solid #1f3a68; padding-bottom: 0.6rem;
                     margin-bottom: 1.2rem; }
      .author-line { color: #6b7280; font-size: 0.85rem; line-height: 1.35; }
      .sec-head { color: #1f3a68; font-size: 1.15rem; font-weight: 700;
                  margin: 1.4rem 0 0.15rem 0; }
      .sec-rule { border: none; border-top: 2px solid #1f3a68; width: 90px;
                  margin: 0 0 0.7rem 0; }
      .finding { padding: 0.55rem 0.8rem; border-radius: 6px;
                 border-left: 4px solid #d99a2b; background: #fdf6ea;
                 margin: 0.45rem 0 0.2rem 0; font-size: 0.95rem; }
      .pending-box { background: #fdf6ea; border: 1px solid #e8c98a;
                     border-left: 4px solid #d99a2b; border-radius: 6px;
                     padding: 0.7rem 0.9rem; margin: 0.5rem 0 0.2rem 0; }
      .done-box { background: transparent; border-left: 3px solid #d8dde5;
                  padding: 0.4rem 0 0.4rem 0.8rem;
                  margin: 0.5rem 0 0.2rem 0; }
      .rejected-box { background: #fbeeee; border-left: 4px solid #c44d4d;
                      border-radius: 6px; padding: 0.7rem 0.9rem;
                      margin: 0.5rem 0 0.2rem 0; opacity: 0.75; }
      .status-pill { display: inline-block; font-size: 0.7rem;
                     padding: 0.1rem 0.45rem; border-radius: 10px;
                     margin-left: 0.4rem; vertical-align: middle; }
      .st-pending   { background: #fdf6ea; color: #8a6a1f; }
      .st-confirmed { background: #edf7f0; color: #2c6b41; }
      .st-edited    { background: #eef2f8; color: #1f3a68; }
      .st-rejected  { background: #fbeeee; color: #8f3535; }
      .st-model     { background: #f0ecf8; color: #5b4a8a; }
      .st-derived   { background: #eef2f8; color: #44506b; }
      .fig-caption { color: #6b7280; font-size: 0.84rem; font-style: italic;
                     text-align: center; margin-top: -0.4rem; }
      .meta-line { color: #6b7280; font-size: 0.82rem; line-height: 1.4; }
      .assumptions { border-left: 3px solid #d99a2b; background: #fdf6ea;
                     padding: 0.7rem 0.9rem; margin-top: 1.2rem;
                     font-size: 0.84rem; color: #44506b; line-height: 1.5; }
    </style>
    """,
    unsafe_allow_html=True,
)


# -- rendering helpers ----------------------------------------------------------

def status_pill(status: str) -> str:
    label, css = STATUS_LABELS.get(status, STATUS_LABELS[PENDING])
    return f'<span class="status-pill {css}">{label}</span>'


def origin_pill(origin: str) -> str:
    if origin == "drafted":
        return '<span class="status-pill st-model">drafted</span>'
    return '<span class="status-pill st-derived">computed</span>'


def box_class(status: str) -> str:
    if status == PENDING:
        return "pending-box"
    if status == REJECTED:
        return "rejected-box"
    return "done-box"


def sec_heading(title: str) -> None:
    st.markdown(f'<div class="sec-head">{title}</div><hr class="sec-rule">',
                unsafe_allow_html=True)


def review_controls(block: Block, key: str) -> None:
    edit_key = f"edit_{key}"
    if st.session_state.get(edit_key, False):
        new_text = st.text_area("Revised text", value=block.display_text,
                                key=f"text_{key}", height=110,
                                label_visibility="collapsed")
        col_save, col_cancel = st.columns([1, 4])
        with col_save:
            if st.button("Save", key=f"save_{key}", width="stretch"):
                block.edited_text = new_text
                block.status = EDITED
                st.session_state[edit_key] = False
                st.rerun()
        with col_cancel:
            if st.button("Cancel", key=f"cancel_{key}"):
                st.session_state[edit_key] = False
                st.rerun()
        return

    col_a, col_b, col_c, _ = st.columns([1, 1, 1, 5])
    with col_a:
        if st.button("Confirm", key=f"ok_{key}", width="stretch",
                     disabled=block.status == CONFIRMED):
            block.status = CONFIRMED
            st.rerun()
    with col_b:
        if st.button("Edit", key=f"ed_{key}", width="stretch"):
            st.session_state[edit_key] = True
            st.rerun()
    with col_c:
        if st.button("Reject", key=f"no_{key}", width="stretch",
                     disabled=block.status == REJECTED):
            block.status = REJECTED
            st.rerun()


def render_document(blocks: list[Block], table_rows: list[dict],
                    date_label: str, by_llm: bool) -> None:
    author = st.session_state.get("author", "")
    contact = st.session_state.get("contact", "")
    sources = st.session_state.get("sources", [])

    st.markdown('<p class="note-title">European Energy Desk Note</p>',
                unsafe_allow_html=True)
    st.markdown('<p class="note-sub">German Power | TTF Gas | EUA Carbon'
                '</p>', unsafe_allow_html=True)
    st.markdown(f'<div class="note-header"><div class="author-line">'
                f'{date_label}'
                + (f'<br>{author} - {contact}' if author else '')
                + '</div></div>', unsafe_allow_html=True)

    reviewable = [b for b in blocks if b.reviewable]
    pending = sum(1 for b in reviewable if b.status == PENDING)
    info_col, btn_col, md_col, pdf_col, _ = st.columns(
        [1.8, 1.3, 1.2, 1.2, 1.6])
    with info_col:
        if reviewable:
            st.caption(f"{len(reviewable) - pending}/{len(reviewable)} blocks reviewed"
                       + (" - drafted by model" if by_llm else ""))
    with btn_col:
        if pending and st.button("Confirm all pending", width="stretch"):
            for b in reviewable:
                if b.status == PENDING:
                    b.status = CONFIRMED
            st.rerun()
    with md_col:
        st.download_button("Download (.md)",
                           data=export_markdown(blocks, date_label, sources),
                           file_name=f"desk_note_{date_label}.md",
                           mime="text/markdown", width="stretch")
    with pdf_col:
        try:
            author_line = st.session_state.get("author", "")
            contact = st.session_state.get("contact", "")
            if author_line and contact:
                author_line = f"{author_line} - {contact}"
            pdf_bytes = note_pdf(blocks, table_rows, date_label, sources,
                                 author=author_line)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"PDF unavailable: {exc}")
        else:
            st.download_button("Download (.pdf)",
                               data=pdf_bytes,
                               file_name=f"desk_note_{date_label}.pdf",
                               mime="application/pdf", width="stretch")

    st.subheader("Market snapshot")
    st.dataframe(table_rows, width="stretch", hide_index=True)
    st.divider()

    if not by_llm:
        st.warning("Model unavailable - the sections below are computed "
                   "summaries from the metrics, not a drafted note.")

    for index, block in enumerate(blocks):
        key = f"b{index}"

        if block.kind == "heading":
            sec_heading(block.content)

        elif block.kind == "callout":
            st.markdown(f'<div class="callout">'
                        f'{cited_html(block.display_text, sources)}'
                        f'{origin_pill(block.origin)}{status_pill(block.status)}'
                        f'</div>', unsafe_allow_html=True)
            if block.reviewable:
                review_controls(block, key)

        elif block.kind == "prose":
            box = box_class(block.status) if block.reviewable else ""
            pills = (f'{origin_pill(block.origin)}{status_pill(block.status)}'
                     if block.reviewable else "")
            st.markdown(f'<div class="{box}">'
                        f'{cited_html(block.display_text, sources)}{pills}</div>',
                        unsafe_allow_html=True)
            if block.reviewable:
                review_controls(block, key)

        elif block.kind == "finding":
            st.markdown(f'<div class="{box_class(block.status)} finding">'
                        f'{cited_html(block.display_text, sources)}'
                        f'{origin_pill(block.origin)}'
                        f'{status_pill(block.status)}</div>',
                        unsafe_allow_html=True)
            if block.reviewable:
                review_controls(block, key)

        elif block.kind == "chart":
            path, caption = block.content
            st.image(str(path), width="stretch")
            st.markdown(f'<div class="fig-caption">{caption}</div>',
                        unsafe_allow_html=True)
            st.write("")

        elif block.kind == "meta":
            st.info(block.display_text)

    assumptions = (
        f"<b>Assumptions:</b> gas plant efficiency {DEFAULT_EFFICIENCY:.0%}, "
        f"emissions factor {DEFAULT_EMISSION_FACTOR} tCO2/MWh, variable O&amp;M "
        f"EUR {DEFAULT_VARIABLE_OPEX:.0f}/MWh. Forward German power prices are "
        "not publicly quoted; the TTF curve shape is used as a directional "
        "proxy only. <b>Data:</b> GIE AGSI+ (storage), ICE Endex TTF via Yahoo "
        "Finance (curve), Energy-Charts / Fraunhofer ISE (German day-ahead), "
        "EEX (EUA carbon)."
    )
    st.markdown(f'<div class="assumptions">{assumptions}</div>',
                unsafe_allow_html=True)


def export_markdown(blocks: list[Block], date_label: str,
                    sources: list[dict]) -> str:
    lines = [f"# European Energy Desk Note - {date_label}", ""]
    for block in blocks:
        if block.kind == "heading":
            lines += [f"## {block.content}", ""]
        elif block.kind in ("callout", "prose"):
            lines += [cited_markdown(block.display_text, sources), ""]
        elif block.kind == "finding":
            lines.append(f"- {cited_markdown(block.display_text, sources)}")
        elif block.kind == "meta":
            lines += ["", block.display_text]
    if sources:
        lines += ["", "## Sources", ""]
        for i, src in enumerate(sources, start=1):
            title = src.get("title") or src["url"]
            lines.append(f"{i}. [{title}]({src['url']})")
        lines.append("")
    if lines and lines[-1] != "":
        lines.append("")
    return "\n".join(lines)


def research_log_tab(result_holder: dict) -> None:
    log_file = result_holder.get("research_log")
    queries, pages, sources = [], [], []
    if result_holder.get("research") is not None:
        queries = result_holder["research"].queries
        pages = result_holder["research"].pages
        sources = result_holder["research"].sources
    elif log_file and Path(log_file).exists():
        data = json.loads(Path(log_file).read_text(encoding="utf-8"))
        queries = data.get("queries", [])
        pages = data.get("pages_visited", [])
        sources = data.get("sources", [])
    with st.expander("Research log - what the model looked at"):
        st.write("**Sources cited in the note**")
        if sources:
            for i, src in enumerate(sources, start=1):
                title = src.get("title") or src["url"]
                st.markdown(f"{i}. [{title}]({src['url']})")
        else:
            st.write("-")
        st.write("**Searches**")
        st.write(queries or "-")
        st.write("**Pages visited**")
        st.write(pages or "-")


@st.cache_resource
def get_monitor() -> DeskMonitor:
    return DeskMonitor(Settings.from_env())


monitor = get_monitor()
settings = monitor.settings

# -- sidebar -------------------------------------------------------------------

with st.sidebar:
    st.header("Run settings")
    force_refresh = st.checkbox("Force refresh market data", value=False)

    with st.expander("Note header"):
        st.session_state.setdefault("author", "")
        st.session_state.setdefault("contact", "")
        st.session_state["author"] = st.text_input("Author",
                                                   value=st.session_state["author"])
        st.session_state["contact"] = st.text_input("Contact",
                                                    value=st.session_state["contact"])

    st.session_state.setdefault("custom_sources", "")
    custom_sources = st.text_area(
        "Custom sources (one link per line)",
        value=st.session_state["custom_sources"], height=90,
        help="Leave empty to use the built-in energy sources. Each link "
             "defines a navigable zone: a site root = the whole site, a "
             "section = that section and everything nested under it, an "
             "article = that article only. Filling this replaces the "
             "built-in sources.",
    )
    st.session_state["custom_sources"] = custom_sources

    unrestricted = st.checkbox(
        "Unrestricted research (any website)", value=False,
        help="Lets the model open anything the searches surface, beyond "
             "your sources and the built-in allowlist. Overrides custom "
             "sources when ticked.",
    )

    st.divider()
    st.caption(f"GIE key: {'OK' if settings.has_gie else 'missing (cache fallback)'}")
    st.caption(f"Gemini key: {'OK' if settings.has_llm else 'missing (no note)'}")

    run_clicked = st.button("Run today's monitor", type="primary",
                            width="stretch")

# -- main page -------------------------------------------------------------------

st.title("EU Gas / Power Desk Monitor")
st.caption("Gas (TTF) + carbon (EUA) -> German power. Public data, free LLM, "
           "drafted desk note reviewed by an analyst.")

if run_clicked or "blocks" in st.session_state:
    if run_clicked:
        try:
            with st.spinner("Running the monitor..."):
                st.session_state["run_result"] = monitor.run(
                    do_research=True, force_refresh=force_refresh,
                    custom_sources=st.session_state["custom_sources"],
                    unrestricted=unrestricted,
                )
        except DataError as exc:
            st.error(f"Run failed: {exc}")
            st.stop()

        result = st.session_state["run_result"]
        for warning in result.warnings:
            st.warning(warning)
        charts = {
            key: Path(result.artifacts[filename])
            for key, (filename, _) in CHART_FILES.items()
            if filename in result.artifacts
        }
        st.session_state["blocks"] = build_blocks(
            result.snapshot.to_dict(), result.note, charts,
        )
        st.session_state["doc_date"] = result.snapshot.as_of
        st.session_state["table_rows"] = snapshot_table(result.snapshot)
        st.session_state["by_llm"] = result.note is not None
        st.session_state["research_log"] = result.artifacts.get("research_log")
        st.session_state["research_ctx"] = result.research
        st.session_state["sources"] = (
            result.research.sources if result.research is not None else []
        )

    blocks = st.session_state["blocks"]
    tab_note, tab_sources = st.tabs(["Desk note", "Sources"])
    with tab_note:
        render_document(
            blocks, st.session_state["table_rows"],
            st.session_state["doc_date"], st.session_state["by_llm"],
        )
    with tab_sources:
        holder = {
            "research": st.session_state.get("research_ctx"),
            "research_log": st.session_state.get("research_log"),
        }
        research_log_tab(holder)

else:
    saved = monitor.latest_saved_run()
    if saved is None:
        st.info("Click **Run today's monitor** in the sidebar to generate "
                "today's pack.")
    else:
        metrics, note, charts = load_saved_note(saved.directory)
        st.warning(f"**Backup view** - you are looking at the last generated "
                   f"pack ({saved.date}), not a live run. Press **Run today's "
                   f"monitor** in the sidebar for fresh numbers.")
        blocks = build_blocks(metrics, note, charts)
        table_rows = json.loads(
            (saved.directory / "snapshot_table.json").read_text(encoding="utf-8")
        ) if (saved.directory / "snapshot_table.json").exists() else []
        log_file = saved.directory / "research_log.json"
        saved_sources = json.loads(log_file.read_text(encoding="utf-8")).get(
            "sources", []) if log_file.exists() else []
        st.session_state["sources"] = saved_sources

        tab_note, tab_sources = st.tabs(["Desk note", "Sources"])
        with tab_note:
            render_document(blocks, table_rows, saved.date, note is not None)
        with tab_sources:
            research_log_tab({"research_log": log_file})
