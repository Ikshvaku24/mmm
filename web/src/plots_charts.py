

# import matplotlib.pyplot as plt
# import matplotlib.dates as mdates
# import seaborn as sns
# import streamlit as st
# import pandas as pd
# import plotly.graph_objects as go
# def generate_heatmap(df):
#     #st.write("Generating heatmap...")
#     plt.figure(figsize=(26,6))
#     sns.heatmap(df.corr(), annot=True, cmap="coolwarm", fmt=".2f")
#     plt.title("Pearson Correlation Matrix")
#     st.pyplot(plt)
# def create_line_chart(pdf):
#     #st.write("Generating line chart...")
#     # Ensure sorted by time
#     pdf = pdf.sort_values("week_start")
#     fig = go.Figure()
#     for col in pdf.columns:
#         if col != "week_start":
#             non_zero_count = ((pdf[col] != 0) & (pdf[col].notna())).sum()
#             label_name = f"{col} (n={non_zero_count})"
#             fig.add_trace(
#                 go.Scatter(
#                     x=pdf["week_start"],
#                     y=pdf[col],
#                     mode="lines+markers",
#                     name=label_name
#                 )
#             )
#     fig.update_layout(
#         title="Trend by Group",
#         xaxis_title="Week",
#         yaxis_title="KPI Value",
#         legend_title="Groups",
#         height=600,
#         hovermode="x unified"
#     )
#     fig.update_xaxes(tickangle=45)
#     st.plotly_chart(fig, use_container_width=True)
# def generate_pie_chart(pdf):
#     # Aggregate at group level
#     summary = (
#         pdf.groupby("final_group")[["kpi_value", "net_spend"]]
#         .sum()
#         .reset_index()
#     )
#     labels = summary["final_group"]
#     # Create 2 pie charts in one figure
#     fig, axes = plt.subplots(1, 2, figsize=(10, 6))
#     # KPI pie chart
#     axes[0].pie(summary["kpi_value"], labels=labels, autopct='%1.1f%%')
#     axes[0].set_title("KPI Distribution")
#     # Net Spend pie chart
#     axes[1].pie(summary["net_spend"], labels=labels, autopct='%1.1f%%')
#     axes[1].set_title("Net Spend Distribution")
#     st.pyplot(plt)
