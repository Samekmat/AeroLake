from pathlib import Path

import folium
import streamlit as st
from jinja2 import Template

_STYLES_DIR = Path(__file__).parent


def load_css(filename: str) -> str:
    return (_STYLES_DIR / filename).read_text(encoding="utf-8")


def load_script_template(filename: str) -> Template:
    return Template(load_css(filename))


def inject_streamlit_css(filename: str) -> None:
    st.markdown(f"<style>{load_css(filename)}</style>", unsafe_allow_html=True)


def folium_style_element(filename: str) -> folium.Element:
    return folium.Element(f"<style>{load_css(filename)}</style>")
