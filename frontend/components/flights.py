import pandas as pd
import streamlit as st

DEPARTURE_COLUMNS = [
    "flight_code",
    "airline_name",
    "arrival_airport",
    "arr_airport_name",
    "scheduled_departure_utc",
    "scheduled_arrival_utc",
    "departure_delay_mins",
    "arrival_delay_mins",
    "status",
]

ARRIVAL_COLUMNS = [
    "flight_code",
    "airline_name",
    "departure_airport",
    "dep_airport_name",
    "last_updated_utc",
    "status",
    "lat",
    "lng",
]

DEPARTURE_COLUMN_LABELS = {
    "flight_code": "Numer lotu",
    "airline_name": "Linia lotnicza",
    "arrival_airport": "Kod lotniska",
    "arr_airport_name": "Lotnisko docelowe",
    "scheduled_departure_utc": "Planowany odlot",
    "scheduled_arrival_utc": "Planowany przylot",
    "departure_delay_mins": "Opóźnienie odlotu (min)",
    "arrival_delay_mins": "Opóźnienie przylotu (min)",
    "status": "Status",
}

ARRIVAL_COLUMN_LABELS = {
    "flight_code": "Numer lotu",
    "airline_name": "Linia lotnicza",
    "departure_airport": "Kod lotniska",
    "dep_airport_name": "Lotnisko pochodzenia",
    "last_updated_utc": "Ostatnia aktualizacja",
    "status": "Status",
    "lat": "Szerokość geogr.",
    "lng": "Długość geogr.",
}


def _column_config(df: pd.DataFrame, labels: dict[str, str]) -> dict[str, st.column_config.Column]:
    return {
        col: st.column_config.Column(label=label)
        for col, label in labels.items()
        if col in df.columns
    }


def _apply_text_filter(df: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    if df.empty or not value or column not in df.columns:
        return df
    return df[df[column].astype(str).str.contains(value, case=False, na=False)]


def _apply_airport_filter(
    df: pd.DataFrame, code_column: str, name_column: str, value: str
) -> pd.DataFrame:
    if df.empty or not value:
        return df

    match = df[code_column].astype(str).str.contains(value, case=False, na=False)
    if name_column in df.columns:
        match |= df[name_column].astype(str).str.contains(value, case=False, na=False)
    return df[match]


def _render_flight_table(
    df: pd.DataFrame,
    *,
    title: str,
    sort_column: str,
    airport_code_column: str,
    airport_name_column: str,
    columns: list[str],
    column_labels: dict[str, str],
    airline_filter: str,
    airport_filter: str,
    empty_message: str,
    file_name: str,
    csv_key: str,
) -> None:
    st.subheader(title)
    if df.empty:
        st.info(empty_message)
        return

    display_df = df.copy()
    if sort_column in display_df.columns:
        display_df = display_df.sort_values(sort_column, ascending=False)
    display_df = _apply_text_filter(display_df, "airline_name", airline_filter)
    display_df = _apply_airport_filter(
        display_df, airport_code_column, airport_name_column, airport_filter
    )

    visible = display_df[[col for col in columns if col in display_df.columns]]
    st.dataframe(
        visible,
        use_container_width=True,
        hide_index=True,
        column_config=_column_config(visible, column_labels),
    )
    st.download_button(
        "Pobierz CSV",
        data=visible.to_csv(index=False).encode("utf-8"),
        file_name=file_name,
        mime="text/csv",
        key=csv_key,
    )
    st.caption(f"Liczba lotów: {len(visible)}")


def render(
    schedules_df: pd.DataFrame,
    arrivals_df: pd.DataFrame,
    airline_filter: str,
    destination_filter: str,
    caption: str | None = None,
    empty_departures_message: str = "Brak danych o odlotach z Krakowa na dzis.",
    empty_arrivals_message: str = "Brak danych o przylotach do Krakowa na dzis.",
    key_prefix: str = "flights",
):
    if caption:
        st.caption()

    _render_flight_table(
        schedules_df,
        title="Loty z Krakowa (odloty)",
        sort_column="scheduled_departure_utc",
        airport_code_column="arrival_airport",
        airport_name_column="arr_airport_name",
        columns=DEPARTURE_COLUMNS,
        column_labels=DEPARTURE_COLUMN_LABELS,
        airline_filter=airline_filter,
        airport_filter=destination_filter,
        empty_message=empty_departures_message,
        file_name="loty_z_krakowa.csv",
        csv_key=f"{key_prefix}_departures_csv",
    )
    _render_flight_table(
        arrivals_df,
        title="Loty do Krakowa (przyloty)",
        sort_column="last_updated_utc",
        airport_code_column="departure_airport",
        airport_name_column="dep_airport_name",
        columns=ARRIVAL_COLUMNS,
        column_labels=ARRIVAL_COLUMN_LABELS,
        airline_filter=airline_filter,
        airport_filter=destination_filter,
        empty_message=empty_arrivals_message,
        file_name="loty_do_krakowa.csv",
        csv_key=f"{key_prefix}_arrivals_csv",
    )
