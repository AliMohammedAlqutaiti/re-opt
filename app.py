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
# Operational Shift: Predictive Maintenance, Trackers & SCADA Insights
# ============================================================

st.set_page_config(
    page_title="RE-OPT Enterprise V3.12 — Solar Operations Digital Twin",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ RE-OPT Enterprise: Autonomous AI Energy Operations Agent")
st.markdown(
    "التوأم الرقمي المؤسسي لمحطات الطاقة الشمسية الضخمة (Utility-Scale Solar PV) — "
    "تحويل بيانات SCADA المعقدة إلى تقارير تنفيذية عالية المستوى، كشف انحرافات المتبعات (Trackers)، "
    "وتحليل طاقة حظر الرياح (Wind-Stow) بدلاً من الانشغال بالتنظيف اليومي المستمر."
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

    def update_telemetry(self, v_mp_v, i_mp_a, soiling_factor=1.0, inject_fault=False):
        self.dc_voltage_v = v_mp_v if not inject_fault else v_mp_v * 0.4
        self.dc_current_a = (i_mp_a * soiling_factor) if not inject_fault else 0.5
        self.dc_power_kw = (self.dc_voltage_v * self.dc_current_a) / 1000.0
        self.health_status = "NORMAL" if not inject_fault else "BLOWN_STRING_FUSE"


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

    def update_telemetry(self, actual_angle, target_angle, wind_speed, inject_misalignment=False, comm_online=True):
        self.comm_status = "ONLINE" if comm_online else "OFFLINE"
        if not comm_online:
            self.active_alarms = ["COMMUNICATION_TIMEOUT"]
            self.health_score = 0.0
            return

        # Automatic Wind-Stow Safety Trigger (> 11.0 m/s wind threshold)
        if wind_speed >= 11.0:
            self.wind_stow_active = True
            self.operating_mode = "WIND_STOW"
            self.target_angle_deg = 0.0
            self.actual_angle_deg = 0.0
            self.position_deviation_deg = 0.0
        elif inject_misalignment:
            self.wind_stow_active = False
            self.operating_mode = "MECHANICAL_STALL"
            self.target_angle_deg = target_angle
            self.actual_angle_deg = target_angle - 18.5  # Stuck tracker angle
            self.position_deviation_deg = 18.5
            self.motor_status = "STALLED"
        else:
            self.wind_stow_active = False
            self.operating_mode = "AUTO_TRACKING"
            self.target_angle_deg = target_angle
            self.actual_angle_deg = target_angle + float(np.random.uniform(-0.4, 0.4))
            self.position_deviation_deg = abs(self.actual_angle_deg - self.target_angle_deg)
            self.motor_status = "HEALTHY"

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
        if self.wind_stow_active:
            self.active_alarms.append("WIND_STOW_ACTIVE")
        self.health_score = max(0.0, score)


class InverterDigitalTwin:
    """Digital representation of one inverter block (Level 3 Hierarchy)"""
    def __init__(self, asset_id, name, dc_capacity_mw, ac_capacity_mw, initial_soiling=1.5):
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
        self.recommendation = "OPTIMAL_OPERATIONS"
        self.confidence = 99.0
        self.expected_recovery_mw = 0.0
        self.revenue_recovery_omr = 0.0
        self.net_benefit_omr = 0.0
        self.reason_codes = []


# ============================================================
# SIDEBAR CONTROLS & PARAMS
# ============================================================

st.sidebar.subheader("🌍 إعدادات المحطة والموقع")
site_name = st.sidebar.text_input("اسم المحطة", value="Utility PV Plant (560MW Capable / 500MW Grid)")
lat = st.sidebar.number_input("خط العرض", value=18.1500, format="%.4f")
lon = st.sidebar.number_input("خط الطول", value=55.1800, format="%.4f")

total_capacity_mw = 560.0
export_limit_mw = 500.0

num_blocks = st.sidebar.selectbox("عدد Inverter Blocks للعرض التفاعلي", [2, 4, 6], index=1)
tariff = st.sidebar.number_input("تعرفة الكهرباء (ر.ع / kWh)", value=0.025, format="%.3f")
cleaning_cost = st.sidebar.number_input("تكلفة الصيانة/التنظيف الميكانيكي للبلوك (ر.ع)", value=20.0, step=5.0)

st.sidebar.subheader("⚙️ محاكاة الأعطال الميدانية (SCADA Simulation)")
dust_accumulation_speed = st.sidebar.slider("معدل الغبار اليومي (%)", 0.001, 0.05, 0.005, 0.001, format="%.3f")
inject_tracker_fault = st.sidebar.toggle("🚨 محاكاة انحراف متبع (Tracker Fault in B02)", value=True)
inject_string_fault = st.sidebar.toggle("⚡ محاكاة عطل سلسلة (String Fault in B01)", value=False)

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
            initial_soiling=1.5,
        )


# ============================================================
# PHYSICS & DIAGNOSTICS ENGINE
# ============================================================

def estimate_module_temperature(ambient_temp_c, irradiance_w_m2, wind_speed_m_s):
    temp_rise = max(8.0, (0.025 * irradiance_w_m2) - (0.55 * wind_speed_m_s))
    return ambient_temp_c + temp_rise

def calculate_expected_power(irradiance_w_m2, module_temp_c, dc_capacity_mw):
    irradiance_factor = np.clip(irradiance_w_m2 / 1000.0, 0.0, 1.2)
    temp_factor = max(0.0, 1.0 + (-0.0035 * (module_temp_c - 25.0)))
    return max(0.0, dc_capacity_mw * irradiance_factor * temp_factor)

def update_digital_twin(twin, weather, dust_speed, tracker_fault, string_fault):
    twin.irradiance_w_m2 = weather["irradiance"]
    twin.ambient_temp_c = weather["temperature"]
    twin.wind_speed_m_s = weather["wind_speed"]
    twin.humidity_pct = weather["humidity"]

    # Soiling is minimal due to nightly robot sweeps
    twin.soiling_pct = float(np.clip(twin.soiling_pct + dust_speed, 0.5, 4.0))
    twin.module_temp_c = estimate_module_temperature(twin.ambient_temp_c, twin.irradiance_w_m2, twin.wind_speed_m_s)

    # Trackers Update
    target_angle = max(-45.0, min(45.0, (twin.irradiance_w_m2 / 1000.0) * 45.0))
    has_faulty_tracker = False
    for idx, trk in enumerate(twin.trackers):
        fault_flag = tracker_fault and (twin.asset_id == "B02.A01.E01") and (idx == 0)
        trk.update_telemetry(0.0, target_angle, twin.wind_speed_m_s, inject_misalignment=fault_flag)
        if trk.position_deviation_deg > 3.0:
            has_faulty_tracker = True

    # Strings Update
    soiling_factor = 1.0 - (twin.soiling_pct / 100.0)
    has_faulty_string = False
    for idx, string in enumerate(twin.strings):
        s_fault = string_fault and (twin.asset_id == "B01.A01.E01") and (idx < 2)
        string.update_telemetry(v_mp_v=680.0, i_mp_a=11.2, soiling_factor=soiling_factor, inject_fault=s_fault)
        if string.health_status != "NORMAL":
            has_faulty_string = True

    twin.soiling_loss_pct = twin.soiling_pct
    twin.temperature_loss_pct = max(0.0, (twin.module_temp_c - 25.0) * 0.35)
    twin.electrical_loss_pct = 1.3

    twin.expected_power_mw = calculate_expected_power(twin.irradiance_w_m2, twin.module_temp_c, twin.dc_capacity_mw)

    # Apply losses based on mechanical/electrical faults
    derate = 1.0 - (twin.soiling_pct / 100.0) - (twin.electrical_loss_pct / 100.0)
    if has_faulty_tracker:
        derate -= 0.15  # 15% drop due to misaligned row
    if has_faulty_string:
        derate -= 0.11  # 11% drop due to blown string fuses

    twin.actual_power_mw = max(0.0, twin.expected_power_mw * derate)

    if twin.expected_power_mw > 0:
        twin.performance_ratio_pct = np.clip((twin.actual_power_mw / twin.expected_power_mw) * 100.0, 0.0, 100.0)
    else:
        twin.performance_ratio_pct = 0.0

    # Decision Engine Reprioritization
    twin.reason_codes = []
    if twin.wind_speed_m_s >= 11.0:
        twin.recommendation = "DELAY (Safety Wind-Stow Active)"
        twin.confidence = 98.0
        twin.reason_codes.append("HIGH_WIND_SAFETY_STOW_LOCK")
    elif has_faulty_tracker:
        twin.recommendation = "DISPATCH_MECHANICAL_MAINTENANCE"
        twin.confidence = 96.0
        twin.reason_codes.append("TRACKER_MISALIGNMENT_STALL")
    elif has_faulty_string:
        twin.recommendation = "INSPECT_STRING_FUSES"
        twin.confidence = 95.0
        twin.reason_codes.append("STRING_CURRENT_IMBALANCE")
    else:
        twin.recommendation = "OPTIMAL_OPERATIONS"
        twin.confidence = 99.0
        twin.reason_codes.append("NIGHTLY_CLEANING_VERIFIED")

    recoverable = max(0.0, twin.expected_power_mw - twin.actual_power_mw)
    twin.expected_recovery_mw = recoverable
    twin.revenue_recovery_omr = recoverable * 5.5 * 1000.0 * tariff
    twin.net_benefit_omr = twin.revenue_recovery_omr - cleaning_cost

    health = 100.0 - (15.0 if has_faulty_tracker else 0.0) - (12.0 if has_faulty_string else 0.0) - (twin.soiling_pct * 0.5)
    twin.health_score = max(0.0, health)
    return twin

# Execute calculations across state assets
for asset_id, twin in st.session_state.digital_twins.items():
    update_digital_twin(twin, weather, dust_accumulation_speed, inject_tracker_fault, inject_string_fault)


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
# HUMAN-FRIENDLY AI REPORT GENERATOR
# ============================================================

def run_autonomous_ai_agent(twin, cleaning_cost_val):
    anomalies = []
    for trk in twin.trackers:
        if trk.position_deviation_deg > 3.0:
            anomalies.append(f"Tracker {trk.tracker_id} deviation ({trk.position_deviation_deg:.1f}° vs Target {trk.target_angle_deg:.1f}°)")

    for s in twin.strings:
        if s.health_status != "NORMAL":
            anomalies.append(f"String {s.string_id} current drop ({s.dc_current_a:.1f}A - Blown Fuse)")

    fallback_result = {
        "recommendation": twin.recommendation,
        "verdict": f"Asset {twin.asset_id} is operating at {twin.performance_ratio_pct:.1f}% PR with a deficit of {twin.expected_recovery_mw:.2f} MW.",
        "reason": f"System identified {len(anomalies)} anomaly event(s). Nightly robot cleaning confirmed active.",
        "anomalies": anomalies if anomalies else ["Normal operations; no tracker or string anomalies detected."],
        "financial_loss_omr": round(twin.revenue_recovery_omr, 2),
        "action": f"Dispatch field technician for {twin.recommendation}" if anomalies else "No intervention required.",
        "work_order_id": f"WO-MAINT-{np.random.randint(1000, 9999)}" if anomalies else "N/A"
    }

    if not gemini_api_key or genai is None:
        return fallback_result

    try:
        client = genai.Client(api_key=gemini_api_key)
        prompt = f"""
You are the Executive AI Energy Analyst for a 560 MW Solar PV Plant.
Convert this raw SCADA telemetry into a human-friendly, high-level executive summary ready to present to management.

Raw Telemetry:
- Asset: {twin.asset_id} ({twin.name})
- Expected Power: {twin.expected_power_mw:.2f} MW
- Actual Output: {twin.actual_power_mw:.2f} MW
- Performance Ratio: {twin.performance_ratio_pct:.1f}%
- Generation Deficit: {twin.expected_recovery_mw:.2f} MW
- Daily Financial Risk: {twin.revenue_recovery_omr:.2f} OMR
- Active Anomalies: {json.dumps(anomalies)}
- Weather: Irradiance {twin.irradiance_w_m2:.0f} W/m2, Wind {twin.wind_speed_m_s:.1f} m/s, Temp {twin.ambient_temp_c:.1f} C

Return JSON format:
{{
  "verdict": "One sentence high-level executive summary...",
  "reason": "Detailed engineering breakdown...",
  "anomalies": ["Anomaly 1", "Anomaly 2"],
  "financial_loss_omr": float_value,
  "action": "Clear actionable instruction",
  "work_order_id": "WO-XXXX"
}}
"""
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = getattr(response, "text", "").strip()
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        return json.loads(text.strip())
    except Exception:
        return fallback_result


# ============================================================
# VISUAL GRAPHICS AND SLD RENDERERS
# ============================================================

def generate_inverter_svg(twin):
    decision_color = "#3b82f6"
    if "MAINTENANCE" in twin.recommendation or "FUSES" in twin.recommendation:
        decision_color = "#ef4444"
    elif "Wind-Stow" in twin.recommendation:
        decision_color = "#f59e0b"
    elif twin.recommendation == "OPTIMAL_OPERATIONS":
        decision_color = "#10b981"

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
    st.metric("🤖 Cleaning Robots", "Nightly Sweeps OK")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Live SCADA & Digital Twin",
    "🎯 Tracker SCADA Layer",
    "⚡ Single-Line Diagram (SLD)",
    "🤖 Executive AI Report",
    "📋 Work Orders & Audit",
    "📈 Performance Analytics",
])

# ------------------------------------------------------------
# TAB
