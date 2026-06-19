import math

import folium
import polars as pl
import streamlit as st
from branca.element import MacroElement
from streamlit_folium import st_folium

from frontend.data_loader import KRK_LAT, KRK_LNG, pick_flight_track
from frontend.styles import folium_style_element, load_script_template

DEFAULT_MAP_TILES = "CartoDB positron"

PLANE_ICON_SIZE = 64
PLANE_ICON_ANCHOR = PLANE_ICON_SIZE // 2
PLANE_IMG_SIZE = 48
PLANE_ICON_BEARING_OFFSET = -45
PLANE_EMOJI_URL = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/2708.png"


class _FlightTimeSlider(MacroElement):
    """Leaflet time slider — updates trail and plane without Streamlit rerun."""

    _template = load_script_template("flight_time_slider.js.j2")

    def __init__(self, path_data: list[dict], control_id: str):
        super().__init__()
        self.path_data = path_data
        self.control_id = control_id
        self.plane_icon_size = PLANE_ICON_SIZE
        self.plane_icon_anchor = PLANE_ICON_ANCHOR
        self.plane_img_size = PLANE_IMG_SIZE
        self.plane_emoji_url = PLANE_EMOJI_URL
        self.bearing_offset = PLANE_ICON_BEARING_OFFSET


def _track_map_bounds(path_df: pl.DataFrame, padding_ratio: float = 0.15) -> list[list[float]]:
    lats = path_df["lat"].to_list() + [KRK_LAT]
    lons = path_df["lng"].to_list() + [KRK_LNG]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_pad = max((lat_max - lat_min) * padding_ratio, 0.08)
    lon_pad = max((lon_max - lon_min) * padding_ratio, 0.08)
    return [
        [lat_min - lat_pad, lon_min - lon_pad],
        [lat_max + lat_pad, lon_max + lon_pad],
    ]


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _bearing_for_row(rows: list[dict], index: int) -> float:
    row = rows[index]

    if index > 0:
        prev = rows[index - 1]
        return _bearing_deg(prev["lat"], prev["lng"], row["lat"], row["lng"])

    if index + 1 < len(rows):
        nxt = rows[index + 1]
        return _bearing_deg(row["lat"], row["lng"], nxt["lat"], nxt["lng"])

    return 0.0


def _path_points(path_df: pl.DataFrame) -> list[dict]:
    rows = path_df.sort("time_bucket").to_dicts()
    return [
        {
            "lat": float(row["lat"]),
            "lng": float(row["lng"]),
            "label": str(row["time_label"]),
            "bearing": _bearing_for_row(rows, index),
        }
        for index, row in enumerate(rows)
    ]


def _add_map_styles(folium_map: folium.Map) -> None:
    folium_map.get_root().header.add_child(folium_style_element("map.css"))


def _add_krk_marker(folium_map: folium.Map) -> None:
    folium.CircleMarker(
        location=[KRK_LAT, KRK_LNG],
        radius=9,
        color="#dc2626",
        fill=True,
        fill_color="#dc2626",
        fill_opacity=0.95,
        weight=2,
        tooltip="Lotnisko Krakow-Balice (KRK)",
    ).add_to(folium_map)


def _build_krk_map() -> folium.Map:
    folium_map = folium.Map(
        location=[KRK_LAT, KRK_LNG],
        zoom_start=10,
        tiles=DEFAULT_MAP_TILES,
        control_scale=True,
    )
    _add_map_styles(folium_map)
    _add_krk_marker(folium_map)
    return folium_map


def _build_track_map(path_df: pl.DataFrame, flight_code: str) -> folium.Map:
    points = _path_points(path_df)
    last_point = points[-1]

    folium_map = folium.Map(
        location=[last_point["lat"], last_point["lng"]],
        zoom_start=6,
        tiles=DEFAULT_MAP_TILES,
        control_scale=True,
    )
    _add_map_styles(folium_map)

    if len(points) > 1:
        folium.PolyLine(
            locations=[[p["lat"], p["lng"]] for p in points],
            color="#94a3b8",
            weight=3,
            opacity=0.75,
            tooltip="Pelna trasa (ostatni lot)",
        ).add_to(folium_map)

    _add_krk_marker(folium_map)
    _FlightTimeSlider(points, control_id=f"track-{flight_code}").add_to(folium_map)

    folium_map.fit_bounds(_track_map_bounds(path_df))
    return folium_map


def _show_map(folium_map: folium.Map, map_key: str) -> None:
    st_folium(
        folium_map,
        width=None,
        height=620,
        returned_objects=[],
        key=map_key,
    )


def render(flights_df: pl.DataFrame, arrivals_df: pl.DataFrame):
    st.subheader("Mapa lotu")

    selected_flight = (
        st.text_input(
            "Numer lotu",
            value="",
            placeholder="np. FR123",
            key="map_selected_flight",
        )
        .strip()
        .upper()
    )

    if not selected_flight:
        _show_map(_build_krk_map(), map_key="krk_map")
        return

    track = pick_flight_track(flights_df, arrivals_df, selected_flight)
    if track.is_empty():
        st.info(f"Brak danych trasy dla lotu {selected_flight}.")
        _show_map(_build_krk_map(), map_key="krk_map")
        return

    _show_map(
        _build_track_map(track, selected_flight),
        map_key=f"track_{selected_flight}",
    )
