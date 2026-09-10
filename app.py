import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import sqlite3
import hashlib
import json
import math
from datetime import datetime, timezone

# Optional Gemini support
try:
    from google import genai
except Exception:
    genai = None


# ============================================================
# RE-OPT ENTERPRISE V3.7
# AI Energy Operations + Digital Twin + Closed-Loop SCADA
# ============================================================

st.set_page_config(
    page_title="RE-OPT Enterprise V3.7",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ RE-OPT Enterprise: AI Energy Operations & Digital Twin")
st.markdown(
    "التوأم الرقمي المؤسسي — مراقبة أصول الطاقة، نمذجة الأداء، كشف الخسائر، "
    "القرار الاقتصادي، وأوامر الصيانة ضمن حلقة تشغيل مغلقة."
)


# ============================================================
# DATABASE (Updated with auto-schema refresh to prevent column mismatches)
# ============================================================

@st.cache_resource
def init_db():
    conn = sqlite3.connect("re_opt_enterprise.db", check_same_thread=False)

    # Clean recreation of tables to prevent legacy schema column count conflicts
    conn.execute("DROP TABLE IF EXISTS work_orders")
    conn.execute("DROP TABLE IF EXISTS audit_chain")
    conn.execute("DROP TABLE IF EXISTS asset_history")

    conn.execute("""
        CREATE TABLE work_orders (
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
        CREATE TABLE audit_chain (
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
        CREATE TABLE asset_history (
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
# WEATHER
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
            "&current=temperature_2m,wind_speed_10m,relative_humidity_2m,"
            "shortwave_radiation"
        )

        response = requests.get(url, timeout=5)

        if response.status_code == 200:
            current = response.json().get("current", {})

            return {
                "temperature": float(
                    current.get("temperature_2m", fallback["temperature"])
                ),
                "wind_speed": float(
                    current.get("wind_speed_10m", fallback["wind_speed"])
                ),
                "irradiance": float(
                    current.get(
                        "shortwave_radiation",
                        fallback["irradiance"]
                    )
                ),
                "humidity": float(
                    current.get(
                        "relative_humidity_2m",
                        fallback["humidity"]
                    )
                ),
            }

    except Exception:
        pass

    return fallback


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.subheader("🌍 إعدادات الموقع")

site_name = st.sidebar.text_input(
    "اسم المحطة",
    value="Marmoul Solar Farm (Oman)"
)

lat = st.sidebar.number_input(
    "خط العرض",
    value=18.1500,
    format="%.4f"
)

lon = st.sidebar.number_input(
    "خط الطول",
    value=55.1800,
    format="%.4f"
)

total_capacity_mw = st.sidebar.slider(
    "إجمالي قدرة المحطة AC (MW)",
    50.0,
    500.0,
    150.0,
    50.0
)

num_blocks = st.sidebar.selectbox(
    "عدد Inverter Blocks",
    [2, 4, 6],
    index=1
)

tariff = st.sidebar.number_input(
    "تعرفة الكهرباء (ر.ع / kWh)",
    value=0.025,
    format="%.3f"
)

cleaning_cost = st.sidebar.number_input(
    "تكلفة دورة التنظيف (ر.ع)",
    value=45.0,
    min_value=0.0,
    step=5.0
)

st.sidebar.subheader("⚙️ نموذج تراكم الغبار والتحكم التجريبي")

dust_accumulation_speed = st.sidebar.slider(
    "معدل التراكم الأساسي لكل دورة (%)",
    min_value=0.001,
    max_value=0.05,
    value=0.008,
    step=0.001,
    format="%.3f"
)

enable_test_mode = st.sidebar.toggle("🚨 تفعيل وضع محاكاة الاتساخ السريع (Test Mode)", value=False)
manual_test_soiling = st.sidebar.slider("نسبة اتساخ تجريبية فورية (%)", 0.0, 30.0, 12.0, 0.5)

st.sidebar.subheader("🤖 AI Operations Agent")

gemini_api_key = st.sidebar.text_input(
    "Gemini API Key (اختياري)",
    type="password"
)

model_version = "RE-OPT-DigitalTwin-V3.7"


# ============================================================
# LIVE WEATHER
# ============================================================

weather = fetch_open_meteo_weather(lat, lon)

api_temp = weather["temperature"]
api_wind = weather["wind_speed"]
api_irradiance = weather["irradiance"]
api_humidity = weather["humidity"]

timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# DIGITAL TWIN
# ============================================================

class InverterDigitalTwin:
    """
    Digital representation of one inverter block.
    """

    def __init__(
        self,
        asset_id,
        name,
        dc_capacity_mw,
        ac_capacity_mw,
        initial_soiling=2.0,
    ):
        self.asset_id = asset_id
        self.name = name

        self.dc_capacity_mw = dc_capacity_mw
        self.ac_capacity_mw = ac_capacity_mw

        self.soiling_pct = initial_soiling

        # Live telemetry
        self.irradiance_w_m2 = 0.0
        self.ambient_temp_c = 0.0
        self.module_temp_c = 0.0
        self.wind_speed_m_s = 0.0
        self.humidity_pct = 0.0

        # Performance
        self.expected_power_mw = 0.0
        self.actual_power_mw = 0.0
        self.performance_ratio_pct = 0.0

        # Loss model
        self.soiling_loss_pct = 0.0
        self.temperature_loss_pct = 0.0
        self.electrical_loss_pct = 0.0

        # Condition
        self.health_score = 100.0

        # Decision
        self.recommendation = "DO_NOT_CLEAN"
        self.confidence = 99.0
        self.expected_recovery_mw = 0.0
        self.revenue_recovery_omr = 0.0
        self.net_benefit_omr = 0.0

        # Explainability
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
            "wind_speed_m_s": self.wind_speed_m_s,
            "humidity_pct": self.humidity_pct,
            "expected_power_mw": self.expected_power_mw,
            "actual_power_mw": self.actual_power_mw,
            "performance_ratio_pct": self.performance_ratio_pct,
            "soiling_loss_pct": self.soiling_loss_pct,
            "temperature_loss_pct": self.temperature_loss_pct,
            "electrical_loss_pct": self.electrical_loss_pct,
            "health_score": self.health_score,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "expected_recovery_mw": self.expected_recovery_mw,
            "revenue_recovery_omr": self.revenue_recovery_omr,
            "net_benefit_omr": self.net_benefit_omr,
            "reason_codes": self.reason_codes,
        }


# ============================================================
# SESSION STATE
# ============================================================

if (
    "digital_twins" not in st.session_state
    or len(st.session_state.digital_twins) != num_blocks
):
    block_ac_capacity = total_capacity_mw / num_blocks
    block_dc_capacity = block_ac_capacity * 1.15

    st.session_state.digital_twins = {}

    for i in range(num_blocks):
        asset_id = f"INV-{i + 1:03d}"
        name = f"Inverter Block {i + 1}"

        st.session_state.digital_twins[asset_id] = InverterDigitalTwin(
            asset_id=asset_id,
            name=name,
            dc_capacity_mw=block_dc_capacity,
            ac_capacity_mw=block_ac_capacity,
            initial_soiling=2.0 + (i * 1.5),
        )


# ============================================================
# PHYSICS / PERFORMANCE MODEL
# ============================================================

def estimate_module_temperature(
    ambient_temp_c,
    irradiance_w_m2,
    wind_speed_m_s,
):
    irradiance_component = 0.025 * irradiance_w_m2
    wind_cooling = 0.55 * wind_speed_m_s

    temp_rise = max(
        8.0,
        irradiance_component - wind_cooling
    )

    return ambient_temp_c + temp_rise


def calculate_temperature_loss(
    module_temp_c,
    temperature_coefficient=-0.0035,
):
    return max(
        0.0,
        -(temperature_coefficient * (module_temp_c - 25.0)) * 100.0
    )


def calculate_expected_power(
    irradiance_w_m2,
    module_temp_c,
    dc_capacity_mw,
):
    irradiance_factor = np.clip(
        irradiance_w_m2 / 1000.0,
        0.0,
        1.2
    )

    temperature_coefficient = -0.0035

    temperature_factor = max(
        0.0,
        1.0 + temperature_coefficient * (module_temp_c - 25.0)
    )

    expected = (
        dc_capacity_mw
        * irradiance_factor
        * temperature_factor
    )

    return max(0.0, expected)


def calculate_actual_power(
    expected_power_mw,
    soiling_pct,
    electrical_loss_pct,
):
    soiling_factor = np.clip(
        1.0 - (soiling_pct / 100.0),
        0.0,
        1.0
    )

    electrical_factor = np.clip(
        1.0 - (electrical_loss_pct / 100.0),
        0.0,
        1.0
    )

    actual = (
        expected_power_mw
        * soiling_factor
        * electrical_factor
    )

    return max(0.0, actual)


def calculate_performance_ratio(
    actual_power_mw,
    expected_power_mw,
):
    if expected_power_mw <= 0:
        return 0.0

    return np.clip(
        (actual_power_mw / expected_power_mw) * 100.0,
        0.0,
        110.0
    )


def calculate_health_score(
    performance_ratio_pct,
    soiling_pct,
    electrical_loss_pct,
):
    score = 100.0

    performance_penalty = max(
        0.0,
        98.0 - performance_ratio_pct
    ) * 2.0

    soiling_penalty = soiling_pct * 0.8

    electrical_penalty = electrical_loss_pct * 1.5

    score -= (
        performance_penalty
        + soiling_penalty
        + electrical_penalty
    )

    return float(np.clip(score, 0.0, 100.0))


# ============================================================
# SOILING MODEL
# ============================================================

def update_soiling(
    soil_pct,
    base_accumulation,
    wind_speed,
    humidity_pct,
):
    accumulation = base_accumulation

    if 8.0 <= wind_speed < 18.0:
        accumulation *= 1.20
    elif wind_speed >= 18.0:
        accumulation *= 0.55

    if humidity_pct >= 60.0:
        accumulation *= 1.15

    return float(
        np.clip(
            soil_pct + accumulation,
            0.5,
            35.0
        )
    )


# ============================================================
# DIGITAL TWIN UPDATE
# ============================================================

def update_digital_twin(
    twin,
    weather,
    dust_speed,
    test_mode,
    test_soiling_val,
):
    twin.irradiance_w_m2 = weather["irradiance"]
    twin.ambient_temp_c = weather["temperature"]
    twin.wind_speed_m_s = weather["wind_speed"]
    twin.humidity_pct = weather["humidity"]

    if test_mode:
        twin.soiling_pct = test_soiling_val
    else:
        twin.soiling_pct = update_soiling(
            twin.soiling_pct,
            dust_speed,
            twin.wind_speed_m_s,
            twin.humidity_pct,
        )

    twin.module_temp_c = estimate_module_temperature(
        twin.ambient_temp_c,
        twin.irradiance_w_m2,
        twin.wind_speed_m_s,
    )

    twin.soiling_loss_pct = twin.soiling_pct
    twin.temperature_loss_pct = calculate_temperature_loss(
        twin.module_temp_c
    )
    twin.electrical_loss_pct = 1.3

    twin.expected_power_mw = calculate_expected_power(
        twin.irradiance_w_m2,
        twin.module_temp_c,
        twin.dc_capacity_mw,
    )

    twin.actual_power_mw = calculate_actual_power(
        twin.expected_power_mw,
        twin.soiling_pct,
        twin.electrical_loss_pct,
    )

    twin.performance_ratio_pct = calculate_performance_ratio(
        twin.actual_power_mw,
        twin.expected_power_mw,
    )

    twin.health_score = calculate_health_score(
        twin.performance_ratio_pct,
        twin.soiling_pct,
        twin.electrical_loss_pct,
    )

    return twin


# ============================================================
# ECONOMIC DECISION ENGINE
# ============================================================

def evaluate_asset(
    twin,
    tariff_value,
    cleaning_cost_value,
    wind_speed,
):
    recoverable_mw = max(
        0.0,
        twin.expected_power_mw - twin.actual_power_mw
    )

    operating_hours = 5.5

    recoverable_kwh = (
        recoverable_mw
        * operating_hours
        * 1000.0
    )

    revenue_at_risk = (
        recoverable_kwh * tariff_value
    )

    net_benefit = (
        revenue_at_risk - cleaning_cost_value
    )

    twin.expected_recovery_mw = recoverable_mw
    twin.revenue_recovery_omr = revenue_at_risk
    twin.net_benefit_omr = net_benefit

    reasons = []

    if wind_speed > 16.0:
        decision = "DELAY (Safety Lock)"
        confidence = 95.0
        reasons.append("HIGH_WIND_SAFETY_LOCK")
    elif (
        net_benefit > 35.0
        and twin.soiling_pct > 8.0
    ):
        decision = "CLEAN"
        confidence = 94.0
        reasons.extend([
            "HIGH_SOILING",
            "POSITIVE_NET_BENEFIT",
            "SUITABLE_WIND"
        ])
    elif (
        net_benefit > 0.0
        and twin.soiling_pct > 4.0
    ):
        decision = "DELAY"
        confidence = 78.0
        reasons.extend([
            "MODERATE_SOILING",
            "WAIT_FOR_BETTER_ECONOMIC_WINDOW"
        ])
    else:
        decision = "DO_NOT_CLEAN"
        confidence = 99.0
        reasons.append("CLEANING_NOT_ECONOMIC")

    twin.recommendation = decision
    twin.confidence = confidence
    twin.reason_codes = reasons

    return twin


# ============================================================
# UPDATE ALL TWINS
# ============================================================

for asset_id, twin in st.session_state.digital_twins.items():
    update_digital_twin(
        twin,
        weather,
        dust_accumulation_speed,
        enable_test_mode,
        manual_test_soiling,
    )

    evaluate_asset(
        twin,
        tariff,
        cleaning_cost,
        api_wind,
    )


# ============================================================
# AUDIT CHAIN
# ============================================================

def log_audit_event(
    conn,
    event_id,
    site,
    operator,
    model_ver,
    input_hash,
    twin,
    approval,
    work_order_id,
):
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT current_event_hash
        FROM audit_chain
        ORDER BY ROWID DESC
        LIMIT 1
        """
    )

    last_row = cursor.fetchone()

    previous_hash = (
        last_row[0]
        if last_row
        else "0" * 64
    )

    reason_str = json.dumps(
        {
            "asset": twin.asset_id,
            "soiling_pct": round(twin.soiling_pct, 3),
            "health_score": round(twin.health_score, 2),
            "decision": twin.recommendation,
            "reasons": twin.reason_codes,
            "net_benefit_omr": round(
                twin.net_benefit_omr,
                2
            ),
        },
        sort_keys=True,
    )

    raw_str = (
        f"{previous_hash}|"
        f"{event_id}|"
        f"{timestamp_str}|"
        f"{twin.recommendation}|"
        f"{reason_str}|"
        f"{input_hash}"
    )

    current_hash = hashlib.sha256(
        raw_str.encode()
    ).hexdigest()

    conn.execute(
        """
        INSERT OR REPLACE INTO audit_chain
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            timestamp_str,
            site,
            operator,
            model_ver,
            input_hash,
            twin.recommendation,
            reason_str,
            twin.net_benefit_omr,
            approval,
            work_order_id,
            previous_hash,
            current_hash,
        )
    )

    conn.commit()

    return current_hash


# ============================================================
# HISTORY LOGGER
# ============================================================

def log_asset_history(conn, site, twin):
    conn.execute(
        """
        INSERT INTO asset_history (
            timestamp,
            site_name,
            asset_id,
            soiling_pct,
            irradiance_w_m2,
            ambient_temp_c,
            module_temp_c,
            expected_power_mw,
            actual_power_mw,
            performance_ratio,
            health_score,
            decision
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            site,
            twin.asset_id,
            twin.soiling_pct,
            twin.irradiance_w_m2,
            twin.ambient_temp_c,
            twin.module_temp_c,
            twin.expected_power_mw,
            twin.actual_power_mw,
            twin.performance_ratio_pct,
            twin.health_score,
            twin.recommendation,
        )
    )

    conn.commit()


# ============================================================
# AI AGENT
# ============================================================

def generate_ai_explanation(twin):
    fallback = (
        f"{twin.name} ({twin.asset_id}) has modeled soiling of "
        f"{twin.soiling_pct:.1f}% and a performance ratio of "
        f"{twin.performance_ratio_pct:.1f}%. "
        f"The expected recoverable output is "
        f"{twin.expected_recovery_mw:.2f} MW with an estimated "
        f"revenue recovery of OMR {twin.revenue_recovery_omr:.1f}/day. "
        f"The current recommendation is {twin.recommendation} "
        f"with {twin.confidence:.0f}% confidence."
    )

    if not gemini_api_key or genai is None:
        return fallback

    try:
        client = genai.Client(api_key=gemini_api_key)

        prompt = f"""
You are RE-OPT AI Energy Operations Agent.

Analyze this solar asset Digital Twin:

Asset: {twin.asset_id}
Soiling: {twin.soiling_pct:.2f}%
Irradiance: {twin.irradiance_w_m2:.1f} W/m2
Ambient temperature: {twin.ambient_temp_c:.1f} C
Module temperature: {twin.module_temp_c:.1f} C
Expected power: {twin.expected_power_mw:.2f} MW
Actual power: {twin.actual_power_mw:.2f} MW
Performance ratio: {twin.performance_ratio_pct:.2f}%
Health score: {twin.health_score:.1f}/100
Expected recovery: {twin.expected_recovery_mw:.2f} MW
Revenue recovery: {twin.revenue_recovery_omr:.2f} OMR/day
Net benefit: {twin.net_benefit_omr:.2f} OMR
Wind speed: {twin.wind_speed_m_s:.1f} m/s
Rule-based recommendation: {twin.recommendation}

Give a concise engineering explanation in English.
Do not invent sensor measurements.
Separate modeled values from observed weather values.
"""

        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
            )
        except Exception:
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt,
            )

        text = getattr(response, "text", None)

        if text:
            return text

    except Exception as exc:
        return (
            fallback
            + f"\n\nAI service unavailable; deterministic engine used. "
            f"Technical note: {str(exc)[:120]}"
        )

    return fallback


# ============================================================
# HEADER STATUS
# ============================================================

st.markdown("---")

status_cols = st.columns(4)

with status_cols[0]:
    st.metric(
        "🌡️ Ambient",
        f"{api_temp:.1f} °C"
    )

with status_cols[1]:
    st.metric(
        "☀️ Irradiance",
        f"{api_irradiance:.0f} W/m²"
    )

with status_cols[2]:
    st.metric(
        "💨 Wind",
        f"{api_wind:.1f} m/s"
    )

with status_cols[3]:
    st.metric(
        "💧 Humidity",
        f"{api_humidity:.0f}%"
    )


# ============================================================
# TABS
# ============================================================

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Live SCADA & Digital Twin",
    "🤖 AI Decision Engine",
    "📋 Work Orders",
    "🛡️ Audit Chain",
    "📈 Asset History",
])


# ============================================================
# TAB 1 — LIVE SCADA + DIGITAL TWINS
# ============================================================

with tab1:

    st.subheader(
        "🟢 غرفة عمليات SCADA — Digital Twin Asset State"
    )

    st.caption(
        "القيم البيئية الحالية تأتي من Open-Meteo. "
        "إنتاج الـPV والخسائر الحالية modeled values في هذه النسخة "
        "إلى أن يتم ربط بيانات SCADA حقيقية."
    )

    twin_list = list(
        st.session_state.digital_twins.values()
    )

    total_expected = sum(
        t.expected_power_mw for t in twin_list
    )

    total_actual = sum(
        t.actual_power_mw for t in twin_list
    )

    total_recovery = sum(
        t.expected_recovery_mw for t in twin_list
    )

    clean_count = sum(
        t.recommendation == "CLEAN"
        for t in twin_list
    )

    summary = st.columns(4)

    with summary[0]:
        st.metric(
            "Expected Plant Power",
            f"{total_expected:.1f} MW"
        )

    with summary[1]:
        st.metric(
            "Modeled Actual Power",
            f"{total_actual:.1f} MW"
        )

    with summary[2]:
        st.metric(
            "Recoverable Output",
            f"{total_recovery:.1f} MW"
        )

    with summary[3]:
        st.metric(
            "Cleaning Candidates",
            str(clean_count)
        )

    st.markdown("---")

    card_cols = st.columns(min(num_blocks, 3))

    for i, twin in enumerate(twin_list):

        with card_cols[i % len(card_cols)]:
            decision = twin.recommendation

            with st.container(border=True):
                col_title, col_id = st.columns([2, 1])
                with col_title:
                    st.markdown(f"#### {twin.name}")
                with col_id:
                    st.markdown(f"**`{twin.asset_id}`**")
                
                st.divider()
                
                st.metric("Health Score", f"{twin.health_score:.0f}/100")
                
                st.write(f"**Soiling Level:** {twin.soiling_pct:.2f}%")
                st.write(f"**Expected Power:** {twin.expected_power_mw:.2f} MW")
                st.write(f"**Actual Power:** {twin.actual_power_mw:.2f} MW")
                st.write(f"**Performance Ratio:** {twin.performance_ratio_pct:.1f}%")
                st.write(f"**AI Decision:** `{decision}`")
                st.write(f"**Confidence:** {twin.confidence:.0f}%")

    st.markdown("### 📡 Live Asset Telemetry")

    rows = []

    for twin in twin_list:
        rows.append({
            "Asset": twin.asset_id,
            "Soiling (%)": round(twin.soiling_pct, 2),
            "Irradiance (W/m²)": round(
                twin.irradiance_w_m2,
                0
            ),
            "Module Temp (°C)": round(
                twin.module_temp_c,
                1
            ),
            "Expected (MW)": round(
                twin.expected_power_mw,
                2
            ),
            "Actual (MW)": round(
                twin.actual_power_mw,
                2
            ),
            "PR (%)": round(
                twin.performance_ratio_pct,
                1
            ),
            "Health": round(
                twin.health_score,
                0
            ),
            "Decision": twin.recommendation,
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### 📉 Loss Decomposition")

    loss_rows = []

    for twin in twin_list:
        loss_rows.append({
            "Asset": twin.asset_id,
            "Soiling Loss (%)": round(
                twin.soiling_loss_pct,
                2
            ),
            "Temperature Loss (%)": round(
                twin.temperature_loss_pct,
                2
            ),
            "Electrical Loss (%)": round(
                twin.electrical_loss_pct,
                2
            ),
            "Total Modeled Loss (%)": round(
                twin.soiling_loss_pct
                + twin.temperature_loss_pct
                + twin.electrical_loss_pct,
                2
            ),
        })

    st.dataframe(
        pd.DataFrame(loss_rows),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# TAB 2 — AI DECISION ENGINE
# ============================================================

with tab2:

    st.subheader(
        "🤖 AI Energy Operations Agent"
    )

    asset_options = {
        f"{t.name} — {t.asset_id}": t.asset_id
        for t in st.session_state.digital_twins.values()
    }

    selected_label = st.selectbox(
        "اختر الأصل للتحليل",
        list(asset_options.keys())
    )

    selected_asset_id = asset_options[selected_label]
    selected_twin = st.session_state.digital_twins[
        selected_asset_id
    ]

    st.markdown("### 📋 Digital Twin Asset State Report")

    info_col1, info_col2, info_col3 = st.columns(3)

    with info_col1:
        st.markdown(f"""
        **معلومات الأصل والأداء:**
        * **الاسم:** {selected_twin.name}
        * **المعرف:** `{selected_twin.asset_id}`
        * **مؤشر الصحة:** **{selected_twin.health_score:.1f} / 100**
        * **القدرة (DC / AC):** {selected_twin.dc_capacity_mw:.2f} / {selected_twin.ac_capacity_mw:.2f} MW
        * **نسبة الأداء (PR):** {selected_twin.performance_ratio_pct:.1f}%
        """)

    with info_col2:
        st.markdown(f"""
        **البيانات البيئية الميدانية:**
        * **الإشعاع الشمسي:** {selected_twin.irradiance_w_m2:.0f} W/m²
        * **درجة حرارة الجو:** {selected_twin.ambient_temp_c:.1f} °C
        * **حرارة اللوح الفعلية:** {selected_twin.module_temp_c:.1f} °C
        * **سرعة الرياح:** {selected_twin.wind_speed_m_s:.1f} m/s
        * **الرطوبة النسبية:** {selected_twin.humidity_pct:.0f}%
        """)

    with info_col3:
        st.markdown(f"""
        **تحليل الفاقد والخسائر:**
        * **نسبة الغبار (Soiling):** {selected_twin.soiling_pct:.2f}%
        * **خسارة الغبار:** {selected_twin.soiling_loss_pct:.2f}%
        * **خسارة الحرارة:** {selected_twin.temperature_loss_pct:.2f}%
        * **الخسارة الكهربائية:** {selected_twin.electrical_loss_pct:.2f}%
        """)

    st.markdown("---")

    metric_cols = st.columns(4)

    with metric_cols[0]:
        st.metric(
            "Health Score",
            f"{selected_twin.health_score:.0f}/100"
        )

    with metric_cols[1]:
        st.metric(
            "Soiling Level",
            f"{selected_twin.soiling_pct:.2f}%"
        )

    with metric_cols[2]:
        st.metric(
            "Expected Power",
            f"{selected_twin.expected_power_mw:.2f} MW"
        )

    with metric_cols[3]:
        st.metric(
            "Actual Power",
            f"{selected_twin.actual_power_mw:.2f} MW"
        )

    st.markdown("### 🧠 Decision Explanation")

    explanation = generate_ai_explanation(
        selected_twin
    )

    st.info(explanation)

    st.markdown("### 💰 Economic Case")

    economics = pd.DataFrame([
        {
            "Metric": "Expected recoverable output",
            "Value": f"{selected_twin.expected_recovery_mw:.2f} MW"
        },
        {
            "Metric": "Estimated revenue recovery",
            "Value": (
                f"OMR "
                f"{selected_twin.revenue_recovery_omr:.2f}/day"
            )
        },
        {
            "Metric": "Cleaning cost",
            "Value": f"OMR {cleaning_cost:.2f}"
        },
        {
            "Metric": "Net benefit",
            "Value": f"OMR {selected_twin.net_benefit_omr:.2f}"
        },
        {
            "Metric": "Recommendation",
            "Value": selected_twin.recommendation
        },
        {
            "Metric": "Confidence",
            "Value": f"{selected_twin.confidence:.0f}%"
        },
    ])

    st.table(economics)


# ============================================================
# TAB 3 — WORK ORDERS / CLOSED LOOP
# ============================================================

with tab3:

    st.subheader(
        "📋 Closed-Loop Maintenance & Work Orders"
    )

    selected_label_wo = st.selectbox(
        "اختر الأصل لتنفيذ دورة الصيانة",
        list(asset_options.keys()),
        key="wo_asset"
    )

    selected_asset_id_wo = asset_options[
        selected_label_wo
    ]

    twin_wo = st.session_state.digital_twins[
        selected_asset_id_wo
    ]

    st.markdown(
        f"""
        **Current recommendation:** `{twin_wo.recommendation}`

        **Soiling:** `{twin_wo.soiling_pct:.2f}%`

        **Expected recovery:** `{twin_wo.expected_recovery_mw:.2f} MW`

        **Net benefit:** `OMR {twin_wo.net_benefit_omr:.2f}`
        """
    )

    with st.form(key="cleaning_work_order_form"):
        operator_name = st.text_input(
            "اسم المشرف المسؤول",
            value="Lead Asset Operator"
        )

        approve_cleaning = st.checkbox(
            f"تأكيد إطلاق دورة التنظيف لـ {twin_wo.asset_id}"
        )

        submit_work_order = st.form_submit_button("🚀 إنشاء Work Order وتنفيذ التنظيف")

    if submit_work_order:
        if not approve_cleaning:
            st.warning("⚠️ يجب تحديد مربع التأكيد أولاً للمتابعة وإرسال أمر الشغل.")
        else:
            soil_before = twin_wo.soiling_pct
            power_before = twin_wo.actual_power_mw

            work_order_id = (
                f"WO-CLEAN-{np.random.randint(1000, 9999)}"
            )

            event_id = (
                f"EVT-{np.random.randint(100000, 999999)}"
            )

            twin_wo.soiling_pct = 0.5

            update_digital_twin(
                twin_wo,
                weather,
                0.0,
                enable_test_mode,
                manual_test_soiling,
            )

            evaluate_asset(
                twin_wo,
                tariff,
                cleaning_cost,
                api_wind,
            )

            power_after = twin_wo.actual_power_mw

            recovery_actual = (
                power_after - power_before
            )

            decision_payload = twin_wo.to_dict()

            input_hash = hashlib.sha256(
                json.dumps(
                    {
                        "before_soiling": soil_before,
                        "before_power": power_before,
                        "decision": decision_payload,
                    },
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()

            try:

                db_conn.execute(
                    """
                    INSERT OR REPLACE INTO work_orders
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        work_order_id,
                        timestamp_str,
                        site_name,
                        twin_wo.asset_id,
                        soil_before,
                        "CLEAN",
                        twin_wo.confidence,
                        twin_wo.expected_recovery_mw,
                        twin_wo.revenue_recovery_omr,
                        twin_wo.net_benefit_omr,
                        "Executed & Verified",
                    )
                )

                final_hash = log_audit_event(
                    db_conn,
                    event_id,
                    site_name,
                    operator_name,
                    model_version,
                    input_hash,
                    twin_wo,
                    "Executed Cleaning",
                    work_order_id,
                )

                log_asset_history(
                    db_conn,
                    site_name,
                    twin_wo,
                )

                st.success(
                    f"""
                    ✅ Cleaning completed.

                    Asset: {twin_wo.asset_id}

                    Soiling:
                    {soil_before:.2f}% → {twin_wo.soiling_pct:.2f}%

                    Modeled power:
                    {power_before:.2f} MW → {power_after:.2f} MW

                    Post-cleaning recovery:
                    +{recovery_actual:.2f} MW

                    Work Order:
                    {work_order_id}

                    Audit hash:
                    {final_hash[:16]}...
                    """
                )

            except Exception as exc:
                st.error(
                    f"Work Order error: {exc}"
                )

    df_work_orders = pd.read_sql(
        """
        SELECT *
        FROM work_orders
        ORDER BY ROWID DESC
        """,
        db_conn
    )

    if not df_work_orders.empty:
        st.markdown("### Work Order History")

        st.dataframe(
            df_work_orders,
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# TAB 4 — AUDIT CHAIN
# ============================================================

with tab4:

    st.subheader(
        "🛡️ Tamper-Evident Audit Chain"
    )

    st.caption(
        "كل قرار تشغيلي يمكن ربطه بالأصل، النموذج، "
        "المدخلات، الموافقة، وأمر العمل."
    )

    df_audit = pd.read_sql(
        """
        SELECT *
        FROM audit_chain
        ORDER BY ROWID DESC
        """,
        db_conn
    )

    if not df_audit.empty:
        st.dataframe(
            df_audit,
            use_container_width=True,
            hide_index=True,
        )

    else:
        st.info(
            "لا توجد أحداث مسجلة بعد."
        )


# ============================================================
# TAB 5 — ASSET HISTORY
# ============================================================

with tab5:

    st.subheader(
        "📈 Digital Twin Historical State"
    )

    df_history = pd.read_sql(
        """
        SELECT *
        FROM asset_history
        ORDER BY id DESC
        """,
        db_conn
    )

    if df_history.empty:
        st.info(
            "سيظهر التاريخ بعد تنفيذ دورات التحديث/التنظيف."
        )

    else:

        asset_filter = st.selectbox(
            "اختر الأصل",
            sorted(
                df_history["asset_id"].unique()
            )
        )

        filtered = df_history[
            df_history["asset_id"] == asset_filter
        ].copy()

        filtered["timestamp"] = pd.to_datetime(
            filtered["timestamp"]
        )

        st.markdown(
            f"### {asset_filter} — Historical Performance"
        )

        chart_df = filtered.set_index(
            "timestamp"
        )[
            [
                "soiling_pct",
                "performance_ratio",
                "health_score",
            ]
        ]

        st.line_chart(chart_df)

        power_df = filtered.set_index(
            "timestamp"
        )[
            [
                "expected_power_mw",
                "actual_power_mw",
            ]
        ]

        st.markdown("### Power Performance")

        st.line_chart(power_df)

        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    f"RE-OPT Enterprise {model_version} | "
    f"Site: {site_name} | "
    f"Last update: {timestamp_str}"
)

st.caption(
    "Prototype note: modeled PV output and soiling require "
    "calibration against plant SCADA and field measurements "
    "before operational deployment."
)
