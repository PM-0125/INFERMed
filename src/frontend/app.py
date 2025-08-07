import streamlit as st
from pathlib import Path
from src.retrieval.openfda_api import OpenFDAClient

# Initialize client with default cache directory
cache_dir = Path(__file__).parents[1] / "data" / "openfda"
client = OpenFDAClient(cache_dir=str(cache_dir))

st.title("INFERMed: FAERS Explorer")

drug = st.text_input("Enter a drug name", value="aspirin")
overview = st.selectbox("Select view", [
    "Top Reactions", "Time Trends", "Age Distribution", "Reporter Breakdown"
])

if not drug:
    st.warning("Please enter a drug name to see data.")
elif overview == "Top Reactions":
    with st.spinner("Fetching top reactions..."):
        data = client.get_top_reactions(drug, top_k=10)
        if data:
            fig = client.plot_top_reactions(drug, top_k=10)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"No reaction data found for {drug}.")
elif overview == "Time Trends":
    with st.spinner("Fetching time series..."):
        series = client.get_time_series(drug)
        if series:
            fig = client.plot_time_series(drug)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"No time series data available for {drug}.")
elif overview == "Age Distribution":
    with st.spinner("Fetching age distribution..."):
        dist = client.get_age_distribution(drug, bins=[18, 35, 50, 65, 99])
        if dist:
            fig = client.plot_age_distribution(drug, bins=[18, 35, 50, 65, 99])
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"No age distribution data for {drug}.")
elif overview == "Reporter Breakdown":
    with st.spinner("Fetching reporter breakdown..."):
        breakdown = client.get_reporter_breakdown(drug)
        if breakdown:
            fig = client.plot_reporter_breakdown(drug)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"No reporter breakdown data for {drug}.")

st.markdown("Data source: [OpenFDA FAERS API](https://open.fda.gov/apis/drug/event/)")
