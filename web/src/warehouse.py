# import os
# import databricks.sql as dsql
# import streamlit as st
# import time
# from src.mapping import date_dict,channel_dict
# import pandas as pd
# MAX_RETRIES = 3 # maximum number of connection retry attempts
# RETRY_DELAYS = [1, 2, 4, 8, 16] # exponential backoff delays in seconds
# DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST")
# DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN","")
# DATABRICKS_SQL_HTTP_PATH = "/sql/1.0/warehouses/81e7fef3068efdda"
# def _create_connection():
#     """Create a fresh SQL warehouse connection."""
#     if not DATABRICKS_HOST:
#         raise ValueError("Missing DATABRICKS_HOST configuration")
#     if not DATABRICKS_SQL_HTTP_PATH:
#         raise ValueError("Missing DATABRICKS_SQL_HTTP_PATH configuration")
#     if not DATABRICKS_TOKEN:
#         raise ValueError("Missing DATABRICKS_TOKEN configuration")
#     return dsql.connect(
#         server_hostname=DATABRICKS_HOST,
#         http_path=DATABRICKS_SQL_HTTP_PATH,
#         access_token=DATABRICKS_TOKEN,
#     )
# @st.cache_resource(ttl=600, show_spinner=False)
# def _cached_connection():
#     """Return a cached SQL connection that auto-refreshes every 10 min.
#     Uses @st.cache_resource so the connection object is shared across
#     reruns and fragment reruns, avoiding a SELECT 1 health-check on
#     every single call.  If the connection is stale the caller catches
#     the exception and calls _cached_connection.clear() to force a
#     new one.
#     """
#     last_err = None
#     for attempt in range(MAX_RETRIES):
#         try:
#             return _create_connection()
#         except Exception as e:
#             last_err = e
#             if attempt < MAX_RETRIES - 1:
#                 delay = RETRY_DELAYS[attempt]
#                 st.toast(
#                     f"SQL warehouse starting up\u2026 retrying in {delay}s "
#                     f"(attempt {attempt + 1}/{MAX_RETRIES})",
#                     icon="\u23f3",
#                 )
#                 time.sleep(delay)
#     raise ConnectionError(
#         f"Could not connect to SQL warehouse after {MAX_RETRIES} attempts. "
#         f"Last error: {last_err}"
#     )
# def get_connection():
#     """Return a healthy SQL connection.
#     First tries the cached connection.  If a query later fails with a
#     stale-connection error the page should call
#     ``_cached_connection.clear()`` and retry – but in practice the
#     10-min TTL keeps the connection fresh enough for interactive use.
#     """
#     return _cached_connection()
# def get_bmc_list(vendor,channel):
#     """Fetch the BMC list for a given vendor and channel."""
#     conn = get_connection()
#     query = f"""
#     SELECT
#     DISTINCT bmc
#     FROM hive_metastore.core_database.{channel}_{vendor}
#     Order BY bmc
#     """
#     with conn.cursor() as cursor:
#         cursor.execute(query)
#         result = cursor.fetchall()
#     return [row[0] for row in result]

# def get_channel_list(vendor, channel):
#     if vendor == "mphasize":
#         column_name = "for_mphasize"
#     else:
#         column_name = "channel_name"
#     conn = get_connection()
#     query = f"""
#     SELECT DISTINCT {column_name}
#     FROM hive_metastore.core_database.{channel}_{vendor}
#     ORDER BY {column_name}
#     """
#     with conn.cursor() as cursor:
#         cursor.execute(query)
#         result = cursor.fetchall()
#     return [row[0] for row in result]
# def get_kpi_attributes(vendor, bmc, channel, channel_name):
#     channel = channel.lower()
#     cols_to_keep = channel_dict.get(channel_name, [])
#     if channel == "media" and vendor == "eki":
#         skip_comp_sql = "channel_group IN ('Digital', 'Offline')"
#     conn = get_connection()
#     if vendor == "eki":
#         bmc_sql = f"bmc = '{bmc}' AND "
#         query = f"""
#         SELECT *
#         FROM hive_metastore.core_database.{channel}_{vendor}
#         WHERE channel_name = '{channel_name}'
#         AND {bmc_sql}{skip_comp_sql}
#         LIMIT 1
#         """
#     elif vendor == "mphasize":
#         bmc_sql = f"bmc = '{bmc}'"
#         query = f"""
#         SELECT *
#         FROM hive_metastore.core_database.{channel}_{vendor}
#         WHERE channel_name = '{channel_name}'
#         AND {bmc_sql}
#         LIMIT 1
#         """
#     with conn.cursor() as cursor:
#         cursor.execute(query)
#         result = cursor.fetchall()
#         if result:
#             # convert result in pandas dataframe
#             df = pd.DataFrame(result, columns=[desc[0] for desc in cursor.description])
#             columns = [col for col in df.columns if col in cols_to_keep]
#             # list only columns which are string type
#             columns_str = [col for col in columns if pd.api.types.is_string_dtype(df[col])]
#             # list only columns which are of numeric type and reject boolean type
#             columns_numeric = [col for col in columns if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col])]
#         else:
#             columns_str = []
#             columns_numeric = []
#     return columns_str, columns_numeric
# # def describe_table(vendor, channel):
# #     """Describe the structure of a table by returning its column names."""
# #     channel = channel.lower()
# #     conn = get_connection()
# #     query = f"""
# #     DESCRIBE hive_metastore.core_database.{channel}_{vendor}
# #     """
# #     with conn.cursor() as cursor:
# #         cursor.execute(query)
# #         result = cursor.fetchall()
# #         # fetch column names with their types in dictionary format
# #         column_types = {row[0]: row[1] for row in result}
# #     return column_types
