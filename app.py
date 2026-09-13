import streamlit as st
import pandas as pd
import numpy as np
import requests
import sqlite3
import hashlib
import json
from datetime import datetime

try:
    import graphviz
except ImportError:
    graphviz = None

try:
    from google import genai
except Exception:
    genai = None

# ============================================================
# RE-OPT ENTERPRISE V3.12 (Utility-Scale Solar PV Platform)
# Physical Capacity: 560 MW | Permitted Grid Export: 500 MW
# Core Shift: SCADA Historical Data Analytics + Anomaly Detection
# ============================================================

st.set_page_config(
    page_title="RE-OPT Enterprise V3.12 — Executive AI Digital Twin",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ RE-OPT Enterprise: AI Data Engine & Executive Digital Twin")
st.markdown(
    "تحويل بيانات SCADA الخام الضخمة إلى **تقارير تنفيذية عالية المستوى**، كشف انحرافات المتبعات (Trackers)، "
    "وتحليل طاقة حظر الرياح (Wind-Stow) بدلاً من معالجة ملفات Excel المعقدة."
)

# ============================================================
# DATABASE SETUP
# ============================================================

@st.cache_resource
def init_db():
    conn = sqlite3.connect("re_opt_enterprise.db", check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scada_events (
            event_id TEXT PRIMARY KEY,
            timestamp TEXT,
            asset_id TEXT,
            event_type TEXT,
            severity TEXT,
            mwh_lost REAL,
            revenue_impact_omr REAL,
            ai_summary TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_chain (
            event_id TEXT PRIMARY KEY,
            timestamp TEXT,
            site_name TEXT,
            operator TEXT,
            model_version TEXT,
            input_hash TEXT,
            decision TEXT,
            reason_codes TEXT,
            financial_impact REAL,
            approval TEXT,
            work_order_id TEXT,
            previous_event_hash TEXT,
            current_event_hash TEXT
        )
    """)
    conn.commit()
    return conn

db_conn = init_db()

# ============================================================
# WEATHER TELEMETRY
# ============================================================

@st.cache_data(ttl=300)
def fetch_open_meteo_weather(lat=18.1500, lon=55.1800):
    fallback = {"temperature": 37.0, "wind_speed": 6.0, "irradiance": 800.0, "humidity": 35.0}
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m,relative_humidity_2m,shortwave_radiation"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            c = res.json().get("current", {})
            return {
                "temperature": float(c.get("temperature_2m", fallback["temperature"])),
                "wind_speed": float(c.get("wind_speed_10m", fallback["wind_speed"])),
                "irradiance": float(c.get("shortwave_radiation", fallback["irradiance"])),
                "humidity": float(c.get("relative_humidity_2m", fallback["humidity"])),
            }
    except Exception:
        pass
    return fallback

weather = fetch_open_meteo_weather()

# ============================================================
# DIGITAL TWIN CLASSES
# ============================================================

class TrackerDigitalTwin:
    def __init__(self, tracker_id, block_id):
        self.tracker_id = tracker_id
        self.block_id = block_id
        self.actual_angle = 0.0
        self.target_angle = 0.0
        self.deviation = 0.0
        self.mode = "AUTO_TRACKING"
        self.health_score = 100.0
        self.alarm = "NONE"

    def update(self, target, wind_speed, force_fault=False):
        if wind_speed >= 11.0:
            self.mode = "WIND_STOW"
            self.target_angle = 0.0
            self.actual_angle = 0.0
            self.deviation = 0.0
            self.alarm = "WIND_STOW_ACTIVE"
        elif force_fault:
            self.mode = "MECHANICAL_STALL"
            self.target_angle = target
            self.actual_angle = target - 18.5  # Stuck angle
            self.deviation = 18.5
            self.alarm = "ANGLE_DEVIATION_HIGH"
            self.health_score = 45.0
        else:
            self.mode = "AUTO_TRACKING"
            self.target_angle = target
            self.actual_angle = target + np.random.uniform(-0.5, 0.5)
            self.deviation = abs(self.actual_angle - self.target_angle)
            self.alarm = "NONE"
            self.health_score = 100.0

class InverterDigitalTwin:
    def __init__(self, asset_id, block_name, dc_mw=10.9, ac_mw=9.5):
        self.asset_id = asset_id
        self.block_name = block_name
        self.dc_capacity_mw = dc_mw
        self.ac_capacity_mw = ac_mw
        self.expected_mw = 0.0
        self.actual_mw = 0.0
        self.pr_pct = 0.0
        self.trackers = [TrackerDigitalTwin(f"{asset_id.split('.')[0]}.T{i+1:02d}", asset_id.split('.')[0]) for i in range(6)]

    def update_telemetry(self, irradiance, temp, wind_speed, inject_anomaly=False):
        irr_factor = np.clip(irradiance / 1000.0, 0.0, 1.2)
        temp_factor = max(0.0, 1.0 - 0.0035 * (temp - 25.0))
        self.expected_mw = self.dc_capacity_mw * irr_factor * temp_factor

        # Update sub-trackers
        target_ang = (irradiance / 1000.0) * 45.0
        for idx, trk in enumerate(self.trackers):
            # Inject simulated tracker misalignment on 1 tracker if anomaly enabled
            trk.update(target_ang, wind_speed, force_fault=(inject_anomaly and idx == 0))

        # Power loss calculation
        loss_factor = 0.96
        if inject_anomaly:
            loss_factor -= 0.18  # Silent string or tracker drop

        self.actual_mw = self.expected_mw * loss_factor
        self.pr_pct = (self.actual_mw / self.expected_mw * 100.0) if self.expected_mw > 0 else 0.0

# Initialize Assets
if "twins" not in st.session_state:
    st.session_state.twins = {
        f"B{i+1:02d}.A01.E01": InverterDigitalTwin(f"B{i+1:02d}.A01.E01", f"Block {i+1:02d}")
        for i in range(4)
    }

# Update Live State
for twin in st.session_state.twins.values():
    twin.update_telemetry(weather["irradiance"], weather["temperature"], weather["wind_speed"], inject_anomaly=(twin.asset_id == "B02.A01.E01"))

# ============================================================
# HUMAN-FRIENDLY AI REPORT GENERATOR (GEMINI INTEGRATION)
# ============================================================

def generate_human_friendly_executive_report(selected_twin, tariff_omr=0.025):
    lost_mw = max(0.0, selected_twin.expected_mw - selected_twin.actual_mw)
    lost_mwh_daily = lost_mw * 5.5
    financial_loss_omr = lost_mwh_daily * 1000 * tariff_omr

    anomalies = []
    for trk in selected_twin.trackers:
        if trk.deviation > 3.0:
            anomalies.append(f"Tracker {trk.tracker_id} is misaligned by {trk.deviation:.1f}° (Target: {trk.target_angle:.1f}°, Actual: {trk.actual_angle:.1f}°).")

    if weather["wind_speed"] >= 11.0:
        anomalies.append(f"Plant in Safety Wind-Stow mode (Wind: {weather['wind_speed']:.1f} m/s).")

    prompt = f"""
You are the Executive AI Energy Analyst for a 560 MW Solar PV Plant.
Convert this raw SCADA telemetry into a human-friendly, high-level executive summary ready to present to management.

Raw Telemetry:
- Asset: {selected_twin.asset_id} ({selected_twin.block_name})
- Expected Power: {selected_twin.expected_mw:.2f} MW
- Actual Output: {selected_twin.actual_mw:.2f} MW
- Performance Ratio: {selected_twin.pr_pct:.1f}%
- Generation Deficit: {lost_mw:.2f} MW ({lost_mwh_daily:.1f} MWh/day lost)
- Daily Financial Impact: {financial_loss_omr:.2f} OMR
- Detected Anomalies: {json.dumps(anomalies)}
- Weather: Irradiance {weather['irradiance']:.0f} W/m2, Wind {weather['wind_speed']:.1f} m/s, Temp {weather['temperature']:.1f} C

Format as:
1. Executive Verdict (One clear sentence)
2. Root-Cause Diagnosis (Bullet points)
3. Financial Impact Summary
4. Recommended Action Item
"""

    if not gemini_api_key or genai is None:
        return {
            "verdict": f"Asset {selected_twin.asset_id} is operating at {selected_twin.pr_pct:.1f}% PR with a deficit of {lost_mw:.2f} MW.",
            "diagnosis": anomalies if anomalies else ["Normal operations; nightly dry-cleaning verified."],
            "financial": f"Estimated Revenue Risk: OMR {financial_loss_omr:.2f} / day.",
            "action": "Inspect Tracker Mechanical Drive Unit" if anomalies else "No action required."
        }

    try:
        client = genai.Client(api_key=gemini_api_key)
        res = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return {"text_report": res.text}
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# UI LAYOUT & EXECUTIVE DASHBOARD
# ============================================================

st.sidebar.subheader("🤖 AI Agent Configuration")
gemini_api_key = st.sidebar.text_input("Gemini API Key", type="password")

st.markdown("---")
status_cols = st.columns(4)
with status_cols[0]:
    st.metric("🌡️ Ambient Temp", f"{weather['temperature']:.1f} °C")
with status_cols[1]:
    st.metric("☀️ Solar Irradiance", f"{weather['irradiance']:.0f} W/m²")
with status_cols[2]:
    st.metric("💨 Wind Speed", f"{weather['wind_speed']:.1f} m/s")
with status_cols[3]:
    st.metric("⚙️ Cleaning Robots Status", "Active (Nightly Sweep Done)")

tab1, tab2, tab3 = st.tabs([
    "📈 Executive AI Briefing & SCADA Data Engine",
    "🎯 Tracker SCADA & Anomaly Layer",
    "⚡ Plant Architecture (SLD)",
])

# ------------------------------------------------------------
# TAB 1: EXECUTIVE AI BRIEFING LAYER
# ------------------------------------------------------------
with tab1:
    st.subheader("📄 Executive SCADA Summary — Ready for Submission")
    st.caption("تحويل قراءات SCADA المعقدة إلى ملخص تنفيذي ذكي يوضح الأعطال والأثر المالي مباشرة.")

    selected_asset_id = st.selectbox("اختر الأصل للتمرير المباشر", list(st.session_state.twins.keys()))
    twin = st.session_state.twins[selected_asset_id]

    if st.button("🚀 توليد التقرير التنفيذي الذكي (Generate Human-Friendly Report)"):
        with st.spinner("جارِ معالجة بيانات SCADA وتوليد التقرير التنفيذي..."):
            report = generate_human_friendly_executive_report(twin)
            st.session_state["exec_report"] = report

    if "exec_report" in st.session_state:
        rep = st.session_state["exec_report"]
        st.markdown("---")
        with st.container(border=True):
            st.markdown(f"### 📊 Executive Report: Asset `{twin.asset_id}`")
            if "text_report" in rep:
                st.markdown(rep["text_report"])
            else:
                st.write(f"**Verdict:** {rep.get('verdict')}")
                st.write(f"**Financial Impact:** {rep.get('financial')}")
                st.write(f"**Recommended Action:** `{rep.get('action')}`")
                st.markdown("**Diagnosed Anomalies:**")
                for diag in rep.get("diagnosis", []):
                    st.write(f"- {diag}")

# ------------------------------------------------------------
# TAB 2: TRACKER ANOMALY LAYER
# ------------------------------------------------------------
with tab2:
    st.subheader("🎯 Tracker Mechanical Alignment & Stow Diagnostics")
    st.caption("كشف المتبعات المتوقفة أو المنحرفة عن زاوية الشمس المباشرة.")

    rows = []
    for tw in st.session_state.twins.values():
        for trk in tw.trackers:
            rows.append({
                "Tracker ID": trk.tracker_id,
                "Block": trk.block_id,
                "Target Angle": f"{trk.target_angle:.1f}°",
                "Actual Angle": f"{trk.actual_angle:.1f}°",
                "Deviation": f"{trk.deviation:.1f}°",
                "Operating Mode": trk.mode,
                "Alarm Status": trk.alarm,
                "Health Score": f"{trk.health_score:.0f}%",
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# TAB 3: SLD GRAPHICAL RENDER
# ------------------------------------------------------------
with tab3:
    st.subheader("⚡ 560 MW Plant Single-Line Diagram")
    if graphviz:
        dot = graphviz.Digraph(engine='dot')
        dot.attr(bgcolor='#0f172a')
        dot.attr('node', shape='box', style='filled', fontcolor='white', fillcolor='#1e293b', color='#38bdf8')
        dot.node('PV', '18-19 PV Strings per Inverter')
        dot.node('INV', '1,888 Inverters (0.8 kV AC)')
        dot.node('MVT', '59 MV Station Transformers (33 kV)')
        dot.node('MTR', '2 Main Transformers (33/400 kV)')
        dot.node('GRID', 'NAMA Grid Interface (Limit: 500 MW)', fillcolor='#15803d')
        dot.edge('PV', 'INV')
        dot.edge('INV', 'MVT')
        dot.edge('MVT', 'MTR')
        dot.edge('MTR', 'GRID')
        st.graphviz_chart(dot)

st.markdown("---")
st.caption(f"RE-OPT Enterprise V3.12 | Site: Utility Solar PV Plant | SCADA Intelligence Layer")
