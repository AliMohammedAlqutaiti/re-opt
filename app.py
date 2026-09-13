import streamlit as st
import pandas as pd
import numpy as np
import requests
import sqlite3
import hashlib
import json
import math
from datetime import datetime

# Graphviz check for SLD diagram rendering
try:
    import graphviz
except ImportError:
    graphviz = None

# Gemini SDK check
try:
    from google import genai
except Exception:
    genai = None


# ============================================================
# RE-OPT ENTERPRISE V3.12 (Utility-Scale Solar PV Platform)
# Physical Capacity: 560 MW | Permitted Grid Export: 500 MW
# Blocks: 59 | Inverters: ~1,888 | Robots: ~1,600 | MV Stations: 59
# ============================================================

st.set_page_config(
    page_title="RE-OPT Enterprise V3.12 — Solar Operations Digital Twin",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ RE-OPT Enterprise: Autonomous AI Energy Operations Agent")
st.markdown(
    "التوأم الرقمي المؤسسي لمحطات الطاقة الشمسية الضخمة (Utility-Scale Solar PV) — "
    "ربط بيانات SCADA العواكس، المتبعات (Trackers)، السلاسل (PV Strings)، معالجة انحرافات الأداء، وسلسلة التدقيق."
)


# ============================================================
# DATABASE SETUP & INITIALIZATION
# ============================================================

@st.cache_resource
def init_db():
    conn = sqlite3.connect("re_opt_enterprise.db", check_same_thread=False)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS work_orders (
            work_order_id TEXT PRIMARY KEY,
            timestamp TEXT,
            site_name TEXT,
            target_block TEXT,
            soil_percentage REAL,
            decision_type TEXT,
            confidence REAL,
            expected_recovery_mw REAL,
            revenue_recovery_omr REAL,
            net_benefit REAL,
            status TEXT
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS asset_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            site_name TEXT,
            asset_id TEXT,
            soiling_pct REAL,
            irradiance_w_m2 REAL,
            ambient_temp_c REAL,
            module_temp_c REAL,
            expected_power_mw REAL,
            actual_power_mw REAL,
            performance_ratio REAL,
            health_score REAL,
            decision TEXT
        )
    """)

    conn.commit()
    return conn

db_conn = init_db()


# ============================================================
# WEATHER TELEMETRY INTEGRATION
# ============================================================

@st.cache_data(ttl=300)
def fetch_open_meteo_weather(latitude, longitude):
    fallback = {
        "temperature": 37.0,
        "wind_speed": 6.0,
        "irradiance": 800.0,
        "humidity": 35.0,
    }

    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={latitude}&longitude={longitude}"
            "&current=temperature_2m,wind_speed_10m,relative_humidity_2m,shortwave_radiation"
        )
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            current = response.json().get("current", {})
            return {
                "temperature": float(current.get("temperature_2m", fallback["temperature"])),
                "wind_speed": float(current.get("wind_speed_10m", fallback["wind_speed"])),
                "irradiance": float(current.get("shortwave_radiation", fallback["irradiance"])),
                "humidity": float(current.get("relative_humidity_2m", fallback["humidity"])),
            }
    except Exception:
        pass

    return fallback


# ============================================================
# DIGITAL TWIN HIERARCHICAL ASSET CLASSES
# ============================================================

class PVString:
    """Digital Representation of an individual PV String (Level 4 Hierarchy)"""
    def __init__(self, string_id, num_modules=26, module_pmax_w=580):
        self.string_id = string_id
        self.num_modules = num_modules
        self.rated_capacity_kw = (num_modules * module_pmax_w) / 1000.0
        self.dc_voltage_v = 0.0
        self.dc_current_a = 0.0
        self.dc_power_kw = 0.0
        self.health_status = "NORMAL"

    def update_telemetry(self, v_mp_v, i_mp_a, soiling_factor=1.0):
        self.dc_voltage_v = v_mp_v
        self.dc_current_a = i_mp_a * soiling_factor
        self.dc_power_kw = (self.dc_voltage_v * self.dc_current_a) / 1000.0


class TrackerDigitalTwin:
    """Digital representation of an individual Solar Tracker Asset (Level 5 Hierarchy)"""
    def __init__(self, tracker_id, block_id):
        self.tracker_id = tracker_id
        self.block_id = block_id
        self.actual_angle_deg = 0.0
        self.target_angle_deg = 0.0
        self.position_deviation_deg = 0.0
        self.operating_mode = "AUTO_TRACKING"
        self.motor_status = "HEALTHY"
        self.comm_status = "ONLINE"
        self.wind_stow_active = False
        self.health_score = 100.0
        self.active_alarms = []

    def update_telemetry(self, actual_angle, target_angle, wind_speed, comm_online=True):
        self.comm_status = "ONLINE" if comm_online else "OFFLINE"
        if not comm_online:
            self.active_alarms = ["COMMUNICATION_TIMEOUT"]
            self.health_score = 0.0
            return

        self.actual_angle_deg = actual_angle
        self.target_angle_deg = target_angle
        self.position_deviation_deg = abs(actual_angle - target_angle)

        # Automatic Wind-Stow Safety Trigger (> 11.0 m/s wind threshold)
        if wind_speed >= 11.0:
            self.wind_stow_active = True
            self.operating_mode = "WIND_STOW"
            self.target_angle_deg = 0.0  # Safe flat angle
        else:
            self.wind_stow_active = False
            self.operating_mode = "AUTO_TRACKING"

        self.evaluate_health()

    def evaluate_health(self):
        score = 100.0
        self.active_alarms = []
        if self.operating_mode == "AUTO_TRACKING" and self.position_deviation_deg > 3.0:
            score -= 30.0
            self.active_alarms.append("ANGLE_DEVIATION_EXCEEDED")
        if self.motor_status == "STALLED":
            score -= 60.0
            self.active_alarms.append("MOTOR_MECHANICAL_STALL")
        self.health_score = max(0.0, score)


class InverterDigitalTwin:
    """Digital representation of one inverter block (Level 3 Hierarchy)"""
    def __init__(self, asset_id, name, dc_capacity_mw, ac_capacity_mw, initial_soiling=2.0):
        self.asset_id = asset_id
        self.name = name
        self.dc_capacity_mw = dc_capacity_mw
        self.ac_capacity_mw = ac_capacity_mw
        self.soiling_pct = initial_soiling

        # Nested Sub-Assets (Strings and Trackers)
        self.strings = [PVString(f"{asset_id}-STR-{i+1:02d}") for i in range(18)]
        self.trackers = [TrackerDigitalTwin(f"{asset_id.split('.')[0]}.T{i+1:02d}", asset_id.split('.')[0]) for i in range(6)]

        self.irradiance_w_m2 = 0.0
        self.ambient_temp_c = 0.0
        self.module_temp_c = 0.0
        self.wind_speed_m_s = 0.0
        self.humidity_pct = 0.0

        self.expected_power_mw = 0.0
        self.actual_power_mw = 0.0
        self.performance_ratio_pct = 0.0

        self.soiling_loss_pct = 0.0
        self.temperature_loss_pct = 0.0
        self.electrical_loss_pct = 0.0

        self.health_score = 100.0
        self.recommendation = "DO_NOT_CLEAN"
        self.confidence = 99.0
        self.expected_recovery_mw = 0.0
        self.revenue_recovery_omr = 0.0
        self.net_benefit_omr = 0.0
        self.reason_codes = []

    def to_dict(self):
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "dc_capacity_mw": self.dc_capacity_mw,
            "ac_capacity_mw": self.ac_capacity_mw,
            "soiling_pct": self.soiling_pct,
            "irradiance_w_m2": self.irradiance_w_m2,
            "ambient_temp_c": self.ambient_temp_c,
            "module_temp_c": self.module_temp_c,
            "expected_power_mw": self.expected_power_mw,
            "actual_power_mw": self.actual_power_mw,
            "performance_ratio_pct": self.performance_ratio_pct,
            "health_score": self.health_score,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "net_benefit_omr": self.net_benefit_omr,
        }


# ============================================================
# SIDEBAR CONTROLS & PARAMS
# ============================================================

st.sidebar.subheader("🌍 إعدادات المحطة والموقع")
site_name = st.sidebar.text_input("اسم المحطة", value="Utility PV Plant (560MW Physical / 500MW Grid)")
lat = st.sidebar.number_input("خط العرض", value=18.1500, format="%.4f")
lon = st.sidebar.number_input("خط الطول", value=55.1800, format="%.4f")

total_capacity_mw = 560.0
export_limit_mw = 500.0

num_blocks = st.sidebar.selectbox("عدد Inverter Blocks المصغرة للعرض", [2, 4, 6], index=1)
tariff = st.sidebar.number_input("تعرفة الكهرباء (ر.ع / kWh)", value=0.025, format="%.3f")
cleaning_cost = st.sidebar.number_input("تكلفة التنظيف الميكانيكي لكل بلوك (ر.ع)", value=20.0, step=5.0)

st.sidebar.subheader("⚙️ نموذج الاتساخ وتغيير الرياح")
dust_accumulation_speed = st.sidebar.slider("معدل التراكم الأساسي (%)", 0.001, 0.05, 0.008, 0.001, format="%.3f")
enable_test_mode = st.sidebar.toggle("🚨 تفعيل محاكاة الاتساخ السريع", value=False)
manual_test_soiling = st.sidebar.slider("نسبة اتساخ تجريبية (%)", 0.0, 30.0, 12.0, 0.5)

st.sidebar.subheader("🤖 AI Operations Agent")
gemini_api_key = st.sidebar.text_input("Gemini API Key", type="password")
model_version = "RE-OPT-DigitalTwin-V3.12"


# ============================================================
# LIVE TELEMETRY & SESSION STATE INITIALIZATION
# ============================================================

weather = fetch_open_meteo_weather(lat, lon)
api_temp = weather["temperature"]
api_wind = weather["wind_speed"]
api_irradiance = weather["irradiance"]
api_humidity = weather["humidity"]
timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

if "digital_twins" not in st.session_state or len(st.session_state.digital_twins) != num_blocks:
    block_ac_capacity = total_capacity_mw / 59.0
    block_dc_capacity = block_ac_capacity * 1.15

    st.session_state.digital_twins = {}
    for i in range(num_blocks):
        asset_id = f"B{i+1:02d}.A01.E01"
        name = f"Inverter Block {i+1:02d}"
        st.session_state.digital_twins[asset_id] = InverterDigitalTwin(
            asset_id=asset_id,
            name=name,
            dc_capacity_mw=block_dc_capacity,
            ac_capacity_mw=block_ac_capacity,
            initial_soiling=2.0 + (i * 1.5),
        )


# ============================================================
# PHYSICS & DECISION ENGINE
# ============================================================

def estimate_module_temperature(ambient_temp_c, irradiance_w_m2, wind_speed_m_s):
    temp_rise = max(8.0, (0.025 * irradiance_w_m2) - (0.55 * wind_speed_m_s))
    return ambient_temp_c + temp_rise

def calculate_expected_power(irradiance_w_m2, module_temp_c, dc_capacity_mw):
    irradiance_factor = np.clip(irradiance_w_m2 / 1000.0, 0.0, 1.2)
    temp_factor = max(0.0, 1.0 + (-0.0035 * (module_temp_c - 25.0)))
    return max(0.0, dc_capacity_mw * irradiance_factor * temp_factor)

def update_digital_twin(twin, weather, dust_speed, test_mode, test_soiling_val):
    twin.irradiance_w_m2 = weather["irradiance"]
    twin.ambient_temp_c = weather["temperature"]
    twin.wind_speed_m_s = weather["wind_speed"]
    twin.humidity_pct = weather["humidity"]

    twin.soiling_pct = test_soiling_val if test_mode else float(np.clip(twin.soiling_pct + dust_speed, 0.5, 35.0))
    twin.module_temp_c = estimate_module_temperature(twin.ambient_temp_c, twin.irradiance_w_m2, twin.wind_speed_m_s)

    # Update Trackers & Strings Sub-Assets
    target_angle = max(-45.0, min(45.0, (twin.irradiance_w_m2 / 1000.0) * 45.0))
    for trk in twin.trackers:
        actual_angle = target_angle if not trk.wind_stow_active else 0.0
        trk.update_telemetry(actual_angle, target_angle, twin.wind_speed_m_s)

    soiling_factor = 1.0 - (twin.soiling_pct / 100.0)
    for string in twin.strings:
        string.update_telemetry(v_mp_v=680.0, i_mp_a=11.2, soiling_factor=soiling_factor)

    twin.soiling_loss_pct = twin.soiling_pct
    twin.temperature_loss_pct = max(0.0, (twin.module_temp_c - 25.0) * 0.35)
    twin.electrical_loss_pct = 1.3

    twin.expected_power_mw = calculate_expected_power(twin.irradiance_w_m2, twin.module_temp_c, twin.dc_capacity_mw)
    twin.actual_power_mw = twin.expected_power_mw * soiling_factor * (1.0 - (twin.electrical_loss_pct / 100.0))

    if twin.expected_power_mw > 0:
        twin.performance_ratio_pct = np.clip((twin.actual_power_mw / twin.expected_power_mw) * 100.0, 0.0, 100.0)
    else:
        twin.performance_ratio_pct = 0.0

    twin.health_score = max(0.0, 100.0 - (twin.soiling_pct * 1.2) - (twin.temperature_loss_pct * 0.5))
    return twin

def evaluate_asset(twin, tariff_value, cleaning_cost_value, wind_speed):
    recoverable_mw = max(0.0, twin.expected_power_mw - twin.actual_power_mw)
    revenue_at_risk = recoverable_mw * 5.5 * 1000.0 * tariff_value
    net_benefit = revenue_at_risk - cleaning_cost_value

    twin.expected_recovery_mw = recoverable_mw
    twin.revenue_recovery_omr = revenue_at_risk
    twin.net_benefit_omr = net_benefit

    reasons = []
    if wind_speed >= 11.0:
        decision = "DELAY (Safety Wind-Stow Lock)"
        confidence = 98.0
        reasons.append("HIGH_WIND_SAFETY_STOW_LOCK")
    elif net_benefit > 15.0 and twin.soiling_pct > 6.0:
        decision = "CLEAN"
        confidence = 94.0
        reasons.extend(["HIGH_SOILING", "POSITIVE_NET_BENEFIT"])
    else:
        decision = "DO_NOT_CLEAN"
        confidence = 99.0
        reasons.append("CLEANING_NOT_ECONOMIC")

    twin.recommendation = decision
    twin.confidence = confidence
    twin.reason_codes = reasons
    return twin

# Execute calculations across state assets
for asset_id, twin in st.session_state.digital_twins.items():
    update_digital_twin(twin, weather, dust_accumulation_speed, enable_test_mode, manual_test_soiling)
    evaluate_asset(twin, tariff, cleaning_cost, api_wind)


# ============================================================
# AUDIT CHAIN ENGINE (SHA-256 LINKED)
# ============================================================

def log_audit_event(conn, event_id, site, operator, model_ver, input_hash, twin, approval, work_order_id):
    cursor = conn.cursor()
    cursor.execute("SELECT current_event_hash FROM audit_chain ORDER BY ROWID DESC LIMIT 1")
    last_row = cursor.fetchone()
    previous_hash = last_row[0] if last_row else "0" * 64

    reason_str = json.dumps({
        "asset": twin.asset_id,
        "soiling_pct": round(twin.soiling_pct, 3),
        "health_score": round(twin.health_score, 2),
        "decision": twin.recommendation,
        "net_benefit_omr": round(twin.net_benefit_omr, 2),
    }, sort_keys=True)

    raw_str = f"{previous_hash}|{event_id}|{timestamp_str}|{twin.recommendation}|{reason_str}|{input_hash}"
    current_hash = hashlib.sha256(raw_str.encode()).hexdigest()

    conn.execute("""
        INSERT OR REPLACE INTO audit_chain
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id, timestamp_str, site, operator, model_ver, input_hash,
        twin.recommendation, reason_str, twin.net_benefit_omr, approval,
        work_order_id, previous_hash, current_hash
    ))
    conn.commit()
    return current_hash


# ============================================================
# AUTONOMOUS AI AGENT INTEGRATION
# ============================================================

def run_autonomous_ai_agent(twin, cleaning_cost_val):
    fallback_result = {
        "recommendation": twin.recommendation,
        "reason": f"Deterministic engine evaluated soiling at {twin.soiling_pct:.2f}% with net benefit of OMR {twin.net_benefit_omr:.2f}.",
        "confidence": int(twin.confidence),
        "expected_recovery_kwh": round(twin.expected_recovery_mw * 5.5 * 1000, 1),
        "risk": "Low" if twin.wind_speed_m_s < 11 else "High (Wind Stow Active)",
        "action": "Deploy dry-cleaning robot" if twin.recommendation == "CLEAN" else "Hold operations",
        "work_order_id": f"WO-CLEAN-{np.random.randint(1000, 9999)}" if twin.recommendation == "CLEAN" else "N/A"
    }

    if not gemini_api_key or genai is None:
        return fallback_result

    try:
        client = genai.Client(api_key=gemini_api_key)
        prompt = f"""
You are the RE-OPT Autonomous AI Energy Operations Agent.
Analyze this utility solar inverter asset and return your response STRICTLY in valid JSON format.

Asset Data:
- Asset ID: {twin.asset_id} ({twin.name})
- DC Capacity: {twin.dc_capacity_mw:.1f} MW | AC Capacity: {twin.ac_capacity_mw:.1f} MW
- Soiling: {twin.soiling_pct:.2f}% | Wind Speed: {twin.wind_speed_m_s:.1f} m/s
- Ambient Temp: {twin.ambient_temp_c:.1f} C | Module Temp: {twin.module_temp_c:.1f} C
- Irradiance: {twin.irradiance_w_m2:.1f} W/m2
- Power Loss: {(twin.expected_power_mw - twin.actual_power_mw):.2f} MW
- Cleaning Cost: {cleaning_cost_val} OMR | Revenue Recovery: {twin.revenue_recovery_omr:.2f} OMR/day
- Net Benefit: {twin.net_benefit_omr:.2f} OMR

JSON format required:
{{
  "recommendation": "CLEAN" or "DO_NOT_CLEAN" or "DELAY",
  "reason": "Detailed engineering explanation...",
  "confidence": integer between 0 and 100,
  "expected_recovery_kwh": float value,
  "risk": "Low" or "Medium" or "High",
  "action": "Deploy dry-cleaning robot" or "Hold operations",
  "work_order_id": "WO-CLEAN-XXXX" or "N/A"
}}
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = getattr(response, "text", "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        return json.loads(text.strip())
    except Exception:
        return fallback_result


# ============================================================
# VISUAL GRAPHICS AND SLD RENDERERS
# ============================================================

def generate_inverter_svg(twin):
    decision_color = "#3b82f6"
    if twin.recommendation == "CLEAN":
        decision_color = "#10b981"
    elif "Safety" in twin.recommendation:
        decision_color = "#f59e0b"

    return f"""
    <svg xmlns="[http://www.w3.org/2000/svg](http://www.w3.org/2000/svg)" viewBox="0 0 400 190" width="100%" height="100%" style="background:#0f172a; border-radius:12px; border:2px solid {decision_color};">
      <g transform="translate(15, 25)">
        <polygon points="0,70 40,10 120,10 160,70" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/>
        <line x1="40" y1="10" x2="80" y2="70" stroke="#38bdf8" stroke-width="1"/>
        <line x1="120" y1="10" x2="80" y2="70" stroke="#38bdf8" stroke-width="1"/>
        <polygon points="0,70 40,10 120,10 160,70" fill="#d97706" opacity="{min(twin.soiling_pct / 20.0, 0.75)}"/>
      </g>
      <g transform="translate(195, 25)">
        <rect x="0" y="0" width="185" height="140" rx="10" fill="#1e293b" stroke="{decision_color}" stroke-width="2"/>
        <text x="92" y="28" font-family="sans-serif" font-size="13" font-weight="bold" fill="#f8fafc" text-anchor="middle">{twin.asset_id}</text>
        <circle cx="92" cy="68" r="24" fill="#0f172a" stroke="{decision_color}" stroke-width="3"/>
        <text x="92" y="73" font-family="sans-serif" font-size="12" font-weight="bold" fill="{decision_color}" text-anchor="middle">{twin.health_score:.0f}%</text>
        <text x="92" y="112" font-family="sans-serif" font-size="11" fill="#94a3b8" text-anchor="middle">PR: {twin.performance_ratio_pct:.1f}%</text>
        <text x="92" y="128" font-family="sans-serif" font-size="10" font-weight="bold" fill="{decision_color}" text-anchor="middle">{twin.recommendation[:22]}</text>
      </g>
    </svg>
    """

def render_plant_sld():
    if graphviz is None:
        st.info("Graphviz package not installed. Install via `pip install graphviz` to view the graphical SLD.")
        return
    dot = graphviz.Digraph(comment='Solar PV Plant SLD', engine='dot')
    dot.attr(rankdir='TB', size='12,8', bgcolor='#0f172a')
    dot.attr('node', shape='box', style='filled', fontname='Helvetica', fontcolor='white', penwidth='2')

    dot.node('PV', '18-19 PV Strings per Inverter\nDC Voltage/Current Telemetry', fillcolor='#334155', color='#38bdf8')
    dot.node('INV', '32-36 Inverters per Block\n(e.g., B01.A01.E01) | 0.8 kV AC', fillcolor='#0284c7', color='#38bdf8')
    dot.node('MVT', '59 MV Station Transformers\n0.8 kV ──► 33 kV', fillcolor='#0369a1', color='#38bdf8')
    dot.node('BUS', '33 kV Collection Network\n(~20 Collector Loops)', fillcolor='#1d4ed8', color='#38bdf8')
    dot.node('MTR', '2 Main Transformers\n33 kV ──► 400 kV Step-Up', fillcolor='#4338ca', color='#38bdf8')
    dot.node('GIS', '400 kV GIS (Gas-Insulated Switchgear)\n2 SF6 Substation Sections', fillcolor='#6d28d9', color='#38bdf8')
    dot.node('GRID', 'Grid Export (NAMA)\nCapable: 560 MW | Limit: 500 MW', fillcolor='#15803d', color='#22c55e')

    dot.node('TRK', 'Solar Trackers (B01.T01)\nWind-Stow: >11 m/s @ 0°', fillcolor='#d97706', color='#f59e0b')
    dot.node('ROB', '1,600 Dry-Cleaning Robots\nNight Operations', fillcolor='#059669', color='#10b981')

    dot.edge('PV', 'INV', label=' DC Power')
    dot.edge('INV', 'MVT', label=' 0.8 kV AC')
    dot.edge('MVT', 'BUS', label=' 33 kV Feeders')
    dot.edge('BUS', 'MTR', label=' 33 kV Busbar')
    dot.edge('MTR', 'GIS', label=' 400 kV HV')
    dot.edge('GIS', 'GRID', label=' Grid Interconnect')

    dot.edge('TRK', 'PV', style='dashed', label=' Mechanical Alignment')
    dot.edge('ROB', 'PV', style='dashed', label=' Surface Cleaning')
    st.graphviz_chart(dot)


# ============================================================
# DASHBOARD UI LAYOUT & TABS
# ============================================================

st.markdown("---")
status_cols = st.columns(4)
with status_cols[0]:
    st.metric("🌡️ Ambient Temp", f"{api_temp:.1f} °C")
with status_cols[1]:
    st.metric("☀️ Irradiance", f"{api_irradiance:.0f} W/m²")
with status_cols[2]:
    st.metric("💨 Wind Speed", f"{api_wind:.1f} m/s")
with status_cols[3]:
    st.metric("💧 Humidity", f"{api_humidity:.0f}%")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Live SCADA & Digital Twin",
    "🎯 Tracker SCADA Layer",
    "⚡ Single-Line Diagram (SLD)",
    "🤖 Autonomous AI Agent",
    "📋 Work Orders & Audit",
    "📈 Performance Analytics",
])

# ------------------------------------------------------------
# TAB 1: SCADA & INVERTER DIGITAL TWIN
# ------------------------------------------------------------
with tab1:
    st.subheader("🟢 غرفة عمليات SCADA — Inverter Block Digital Twin")
    twin_list = list(st.session_state.digital_twins.values())
    total_expected = sum(t.expected_power_mw for t in twin_list)
    total_actual = sum(t.actual_power_mw for t in twin_list)
    clean_count = sum(t.recommendation == "CLEAN" for t in twin_list)

    m_cols = st.columns(4)
    with m_cols[0]:
        st.metric("Modeled Plant Generation", f"{total_actual:.2f} MW")
    with m_cols[1]:
        st.metric("Expected Capacity", f"{total_expected:.2f} MW")
    with m_cols[2]:
        st.metric("Cleaning Candidates", str(clean_count))
    with m_cols[3]:
        st.metric("Grid Limit Status", "NORMAL (500 MW Limit)")

    st.markdown("---")
    card_cols = st.columns(min(num_blocks, 3))
    for i, twin in enumerate(twin_list):
        with card_cols[i % len(card_cols)]:
            with st.container(border=True):
                st.markdown(f"#### {twin.name} (`{twin.asset_id}`)")
                st.write(generate_inverter_svg(twin), unsafe_allow_html=True)
                st.divider()
                st.write(f"**Soiling Level:** {twin.soiling_pct:.2f}%")
                st.write(f"**Modeled Actual Power:** {twin.actual_power_mw:.2f} MW")
                st.write(f"**Performance Ratio:** {twin.performance_ratio_pct:.1f}%")

# ------------------------------------------------------------
# TAB 2: TRACKER SCADA LAYER
# ------------------------------------------------------------
with tab2:
    st.subheader("🎯 واجهة التحكم بالمتبعات — Tracker SCADA Dedicated Layer")
    st.caption("مراقبة حالة المتبعات التشغيلية بشكل مستقل ومتابعة الاستجابة التلقائية لحظر الرياح.")

    tracker_rows = []
    for twin in st.session_state.digital_twins.values():
        for trk in twin.trackers:
            tracker_rows.append({
                "Tracker ID": trk.tracker_id,
                "Block ID": trk.block_id,
                "Actual Angle (°)": round(trk.actual_angle_deg, 1),
                "Target Angle (°)": round(trk.target_angle_deg, 1),
                "Deviation (°)": round(trk.position_deviation_deg, 1),
                "Mode": trk.operating_mode,
                "Wind-Stow Active": "YES 🚨" if trk.wind_stow_active else "NO",
                "Motor Status": trk.motor_status,
                "Comm Status": trk.comm_status,
                "Health Score": trk.health_score,
            })

    st.dataframe(pd.DataFrame(tracker_rows), use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# TAB 3: SINGLE-LINE DIAGRAM (SLD)
# ------------------------------------------------------------
with tab3:
    st.subheader("⚡ المخطط الأحادي للكهرباء — Utility Plant Single-Line Diagram (SLD)")
    st.caption("توصيل الطاقة الكهربائية من الألواح عبر العواكس والمحولات وصولاً إلى شبكة NAMA بقدرة تصدير 500 ميجاواط.")
    render_plant_sld()

# ------------------------------------------------------------
# TAB 4: AUTONOMOUS AI AGENT
# ------------------------------------------------------------
with tab4:
    st.subheader("🤖 Autonomous AI Operations Agent")
    asset_options = {f"{t.name} — {t.asset_id}": t.asset_id for t in st.session_state.digital_twins.values()}
    selected_label = st.selectbox("اختر الأصل للتحليل الذكي", list(asset_options.keys()))
    selected_twin = st.session_state.digital_twins[asset_options[selected_label]]

    if st.button("🚀 تشغيل وكيل الذكاء الاصطناعي المستقل"):
        with st.spinner("جارِ تحليل البيانات الميدانية والتوأم الرقمي..."):
            rep = run_autonomous_ai_agent(selected_twin, cleaning_cost)
            st.session_state[f"agent_rep_{selected_twin.asset_id}"] = rep

    rep_key = f"agent_rep_{selected_twin.asset_id}"
    if rep_key in st.session_state:
        rep = st.session_state[rep_key]
        st.markdown(f"### ⚡ Asset Operational Report: {selected_twin.asset_id}")
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            st.markdown(f"""
            **Asset & Capacity**
            * **Asset ID:** `{selected_twin.asset_id}`
            * **DC Capacity:** {selected_twin.dc_capacity_mw:.1f} MW
            * **AC Capacity:** {selected_twin.ac_capacity_mw:.1f} MW
            * **Health Score:** **{selected_twin.health_score:.0f} / 100**
            """)
        with col_t2:
            st.markdown(f"""
            **Decision & Recovery**
            * **Recommendation:** `{rep.get('recommendation')}`
            * **Confidence:** {rep.get('confidence')}%
            * **Action:** `{rep.get('action')}`
            * **Work Order:** `{rep.get('work_order_id')}`
            """)
        st.info(f"**Reasoning:** {rep.get('reason')}")

# ------------------------------------------------------------
# TAB 5: WORK ORDERS & AUDIT CHAIN
# ------------------------------------------------------------
with tab5:
    st.subheader("📋 Closed-Loop Work Orders & Audit Chain")
    df_audit = pd.read_sql("SELECT * FROM audit_chain ORDER BY ROWID DESC", db_conn)
    if not df_audit.empty:
        st.dataframe(df_audit, use_container_width=True, hide_index=True)
    else:
        st.info("لا توجد أحداث مسجلة في سلسلة التدقيق حتى الآن.")

# ------------------------------------------------------------
# TAB 6: PERFORMANCE ANALYTICS
# ------------------------------------------------------------
with tab6:
    st.subheader("📈 التحليلات المتقدمة والتتبع التاريخي")
    st.caption("متابعة تغير نسبة الاتساخ ومؤشر الأداء للتوأم الرقمي عبر الزمن.")
    df_hist = pd.read_sql("SELECT * FROM asset_history ORDER BY id DESC", db_conn)
    if not df_hist.empty:
        st.dataframe(df_hist, use_container_width=True, hide_index=True)
    else:
        st.info("سيظهر السجل التاريخي فور تنفيذ دورات التنظيف والتحديث.")

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.caption(f"RE-OPT Enterprise {model_version} | Site: {site_name} | Timestamp: {timestamp_str}")
