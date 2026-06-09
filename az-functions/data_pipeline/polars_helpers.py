import polars as pl


def flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
    """Recursively flattens a nested dictionary by joining keys with `sep`."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def resolve_type_conflict(type1, type2):
    """Resolves type conflict between two Polars data types, returning the more general type."""
    if type1 == type2:
        return type1
    if type1 == pl.Int64 and type2 == pl.Float64:
        return pl.Float64
    if type1 == pl.Float64 and type2 == pl.Int64:
        return pl.Float64
    if type1 == pl.String or type2 == pl.String:
        return pl.String
    if type1.is_numeric() and type2.is_numeric():
        return pl.Float64
    return pl.String


def align_dataframe_schemas(dfs: list[pl.DataFrame]) -> list[pl.DataFrame]:
    """Aligns the columns and types of a list of Polars DataFrames so they can be concatenated."""
    if not dfs:
        return []

    unified_schema = {}
    for df in dfs:
        for col, dtype in df.schema.items():
            if col not in unified_schema:
                unified_schema[col] = dtype
            else:
                unified_schema[col] = resolve_type_conflict(unified_schema[col], dtype)

    standardized_dfs = []
    for df in dfs:
        select_exprs = []
        for col, target_dtype in unified_schema.items():
            if col in df.columns:
                select_exprs.append(pl.col(col).cast(target_dtype))
            else:
                select_exprs.append(pl.lit(None).cast(target_dtype).alias(col))
        standardized_dfs.append(df.select(select_exprs))

    return standardized_dfs
