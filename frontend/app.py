import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "frontend._path",
    Path(__file__).with_name("_path.py"),
)
_path = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_path)

import sys
from datetime import date, datetime

import streamlit as st


def _ensure_fresh_frontend_imports() -> None:
    """Drop cached frontend modules after refactors (Streamlit keeps stale imports)."""
    cached_loader = sys.modules.get("frontend.data_loader")
    if cached_loader is not None and hasattr(cached_loader, "filter_dataframe_by_today"):
        return

    for module_name in list(sys.modules):
        if module_name.startswith("frontend.") and not module_name.endswith("._path"):
            del sys.modules[module_name]


_ensure_fresh_frontend_imports()

from frontend import data_loader
from frontend.components import analytics, flights, map_view
from frontend.styles import inject_streamlit_css

st.set_page_config(
    page_title="AeroLake",
    layout="wide",
    page_icon="✈️",
    menu_items={
        "Get help": None,
        "Report a bug": None,
        "About": None,
    },
)

today = datetime.now(data_loader.LOCAL_TZ).date()
today_label = today.strftime("%d.%m.%Y")
tab_today = f"Loty {today_label}"
tab_historical = "Dane historyczne"
tab_map = "Mapa"
tab_analytics = "Analiza"
tab_options = [tab_today, tab_historical, tab_map, tab_analytics]

if "filter_start" not in st.session_state:
    st.session_state.filter_start = data_loader.DATA_START_DATE
if "filter_end" not in st.session_state:
    st.session_state.filter_end = today
if "main_tab" not in st.session_state:
    st.session_state.main_tab = tab_today


def _reset_flight_filters(include_dates: bool) -> None:
    st.session_state.filter_airline = ""
    st.session_state.filter_airport = ""
    if include_dates:
        st.session_state.filter_start = data_loader.DATA_START_DATE
        st.session_state.filter_end = today


def _column_stat(df, column: str, func, default=0):
    if df.is_empty() or column not in df.columns:
        return default
    return func(df[column])


def _set_main_tab(tab: str) -> None:
    st.session_state.main_tab = tab


def _refresh_data() -> None:
    data_loader.clear_cache()


def _render_flight_filters(*, show_dates: bool) -> tuple[str, str, date, date]:
    if show_dates:
        date_cols = st.columns(2)
        with date_cols[0]:
            st.date_input(
                "Data od",
                min_value=data_loader.DATA_START_DATE,
                max_value=today,
                key="filter_start",
                format="DD.MM.YYYY",
            )
        with date_cols[1]:
            st.date_input(
                "Data do",
                min_value=data_loader.DATA_START_DATE,
                max_value=today,
                key="filter_end",
                format="DD.MM.YYYY",
            )

    text_cols = st.columns(2)
    with text_cols[0]:
        st.text_input("Filtr linii lotniczej", value="", key="filter_airline")
    with text_cols[1]:
        st.text_input("Filtr lotniska (cel / pochodzenie)", value="", key="filter_airport")

    st.button(
        "Wyczyść",
        key="clear_filters_historical" if show_dates else "clear_filters_today",
        on_click=_reset_flight_filters,
        args=(show_dates,),
    )

    start_date = st.session_state.filter_start
    end_date = st.session_state.filter_end

    if show_dates and start_date > end_date:
        st.error("Data od nie moze byc pozniejsza niz data do.")
        st.stop()

    return (
        st.session_state.filter_airline,
        st.session_state.filter_airport,
        start_date,
        end_date,
    )


with st.sidebar:
    inject_streamlit_css("sidebar.css")
    for index, tab in enumerate(tab_options):
        st.button(
            tab,
            key=f"nav_{index}",
            use_container_width=True,
            type="primary" if tab == st.session_state.main_tab else "tertiary",
            on_click=_set_main_tab,
            args=(tab,),
        )
    st.divider()
    data_label = f"Dane z: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    meta_cols = st.columns([0.1, 0.9], vertical_alignment="center")
    with meta_cols[0]:
        st.button(
            "↻",
            key="refresh",
            help="Odśwież dane",
            on_click=_refresh_data,
        )
    with meta_cols[1]:
        st.caption(data_label)

st.title("AeroLake")
st.caption("Lotnisko Krakow-Balice (KRK) | Dane od 31.05.2026")

try:
    schedules_all = data_loader.load_schedules()
    arrivals_all = data_loader.load_arrivals()
    flights_all = data_loader.load_live_flights()
except ValueError as exc:
    st.error(f"Błąd konfiguracji: {exc}")
    st.stop()
except Exception as exc:
    st.error(f"Nie udało się wczytać danych: {exc}")
    st.stop()

flights_schedules_df = data_loader.filter_dataframe_by_today(
    schedules_all, "scheduled_departure_utc", on_date=today
)
flights_arrivals_df = data_loader.filter_dataframe_by_today(
    arrivals_all, "last_updated_utc", on_date=today
)

current_tab = st.session_state.main_tab

if current_tab == tab_today:
    airline_filter, destination_filter, _, _ = _render_flight_filters(show_dates=False)
    flights.render(
        schedules_df=flights_schedules_df,
        arrivals_df=flights_arrivals_df,
        airline_filter=airline_filter,
        destination_filter=destination_filter,
        key_prefix="flights_today",
    )

elif current_tab == tab_historical:
    airline_filter, destination_filter, start_date, end_date = _render_flight_filters(
        show_dates=True
    )

    schedules_historical = data_loader.filter_dataframe_by_date(
        schedules_all, "scheduled_departure_utc", start_date, end_date
    )
    arrivals_historical = data_loader.filter_dataframe_by_date(
        arrivals_all, "last_updated_utc", start_date, end_date
    )

    avg_delay = _column_stat(schedules_historical, "departure_delay_mins", lambda col: col.mean())
    active_routes_count = _column_stat(
        schedules_historical, "arrival_airport", lambda col: col.n_unique()
    )

    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Odloty z Krakowa", schedules_historical.height)
    kpi_cols[1].metric("Przyloty do Krakowa", arrivals_historical.height)
    kpi_cols[2].metric("Średnie opóźnienie (min)", f"{avg_delay:.1f}" if avg_delay else "0.0")
    kpi_cols[3].metric("Aktywne kierunki", active_routes_count)

    flights.render(
        schedules_df=schedules_historical,
        arrivals_df=arrivals_historical,
        airline_filter=airline_filter,
        destination_filter=destination_filter,
        empty_departures_message="Brak odlotów z Krakowa w wybranym zakresie dat.",
        empty_arrivals_message="Brak przylotów do Krakowa w wybranym zakresie dat.",
        key_prefix="flights_historical",
    )

elif current_tab == tab_map:
    map_view.render(
        flights_df=flights_all,
        arrivals_df=arrivals_all,
    )

elif current_tab == tab_analytics:
    routes_raw = data_loader.load_gold("active_routes")
    airline_perf_raw = data_loader.load_gold("airline_performance")
    weather_impact_raw = data_loader.load_gold("weather_impact")
    analytics.render(
        schedules_df=schedules_all,
        arrivals_df=arrivals_all,
        routes_df=routes_raw,
        airline_perf_df=airline_perf_raw,
        weather_impact_df=weather_impact_raw,
    )
