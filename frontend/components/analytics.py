import plotly.express as px
import polars as pl
import streamlit as st

from frontend.data_loader import compute_delays_by_hour, compute_weekday_patterns

WEEKDAY_ORDER = [
    "Poniedziałek",
    "Wtorek",
    "Środa",
    "Czwartek",
    "Piątek",
    "Sobota",
    "Niedziela",
]


def _render_delays_by_hour(schedules_df: pl.DataFrame):
    st.subheader("Średnie opóźnienia")
    delays_df = compute_delays_by_hour(schedules_df)

    if delays_df.is_empty():
        st.info("Brak danych o opóźnieniach.")
        return

    fig = px.bar(
        delays_df,
        x="departure_hour_utc",
        y="avg_delay_mins",
        text="flight_count",
        labels={
            "departure_hour_utc": "Godzina odlotu (UTC)",
            "avg_delay_mins": "Średnie opóźnienie (min)",
            "flight_count": "Liczba lotów",
        },
        title="Średnie opóźnienia odlotów",
    )
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


def _render_top_destinations(routes_df: pl.DataFrame):
    st.subheader("Najpopularniejsze kierunki")
    if routes_df.is_empty():
        st.info("Brak danych o trasach.")
        return

    top_routes = routes_df.sort("flight_count", descending=True).head(10)
    fig = px.bar(
        top_routes,
        x="arrival_airport",
        y="flight_count",
        labels={"arrival_airport": "Lotnisko docelowe", "flight_count": "Liczba lotów"},
        title="Top 10 kierunków z KRK",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_weekday_patterns(schedules_df: pl.DataFrame, arrivals_df: pl.DataFrame):
    st.subheader("Rozkład lotów według dnia tygodnia")
    weekday_df = compute_weekday_patterns(schedules_df, arrivals_df)

    if weekday_df.is_empty():
        st.info("Brak danych do analizy dni tygodnia.")
        return

    base_chart_kwargs = {
        "x": "weekday_label",
        "color": "direction",
        "barmode": "group",
        "category_orders": {"weekday_label": WEEKDAY_ORDER},
    }

    avg_col, sum_col = st.columns(2)
    with avg_col:
        avg_fig = px.bar(
            weekday_df,
            y="avg_flights_per_day",
            title="Średnia ilość lotów/dzień",
            labels={
                "weekday_label": "Dzień tygodnia",
                "avg_flights_per_day": "Średnia liczba lotów",
                "direction": "Kierunek",
            },
            **base_chart_kwargs,
        )
        st.plotly_chart(avg_fig, use_container_width=True)

    with sum_col:
        sum_fig = px.bar(
            weekday_df,
            y="flight_count",
            title="Suma lotów/dzień",
            labels={
                "weekday_label": "Dzień tygodnia",
                "flight_count": "Suma lotów",
                "direction": "Kierunek",
            },
            **base_chart_kwargs,
        )
        st.plotly_chart(sum_fig, use_container_width=True)


def _render_airline_performance(airline_perf_df: pl.DataFrame):
    st.subheader("Wydajność linii lotniczych")
    if airline_perf_df.is_empty():
        st.info("Brak danych o liniach lotniczych.")
        return

    top_airlines = airline_perf_df.sort("total_flights", descending=True).head(15)
    fig = px.bar(
        top_airlines,
        x="airline_name",
        y="avg_departure_delay_mins",
        color="total_flights",
        labels={
            "airline_name": "Linia lotnicza",
            "avg_departure_delay_mins": "Średnie opóźnienie (min)",
            "total_flights": "Liczba lotów",
        },
        title="Średnie opóźnienia według linii lotniczej",
    )
    fig.update_layout(xaxis_tickangle=-35)
    st.plotly_chart(fig, use_container_width=True)


def _render_weather_impact(weather_impact_df: pl.DataFrame):
    st.subheader("Wpływ pogody na opóźnienia")
    if weather_impact_df.is_empty():
        st.info("Brak danych o wpływie pogody.")
        return

    for metric, title in [
        ("temperature_celsius", "Temperatura"),
        ("wind_speed_mps", "Prędkość wiatru"),
        ("visibility_level", "Widoczność"),
    ]:
        subset = weather_impact_df.filter(pl.col("metric") == metric)
        if subset.is_empty():
            continue

        fig = px.bar(
            subset,
            x="bucket_label",
            y="avg_departure_delay_mins",
            text="flight_count",
            labels={
                "bucket_label": title,
                "avg_departure_delay_mins": "Średnie opóźnienie (min)",
                "flight_count": "Liczba lotów",
            },
            title=f"Opóźnienia vs {title.lower()}",
        )
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)


def render(
    schedules_df: pl.DataFrame,
    arrivals_df: pl.DataFrame,
    routes_df: pl.DataFrame,
    airline_perf_df: pl.DataFrame,
    weather_impact_df: pl.DataFrame,
):
    st.subheader("Analiza zależności")
    _render_delays_by_hour(schedules_df)
    _render_top_destinations(routes_df)
    _render_weekday_patterns(schedules_df, arrivals_df)
    _render_airline_performance(airline_perf_df)
    _render_weather_impact(weather_impact_df)
