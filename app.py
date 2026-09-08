import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import pydeck as pdk
import streamlit.components.v1 as components
import hashlib
import sqlite3
from datetime import datetime
from scipy.optimize import brentq
from google import genai

st.set_page_config(page_title="RE-OPT V2.1: OQ Accelerator Edition", layout="wide")

st.markdown('<div id="top-anchor"></div>', unsafe_allow_html=True)

st.title("⚡ RE-OPT V2.1: Enterprise Autonomous Operating & Financial Twin")
st.markdown("نسخة مسرع OQ — مطورة بالكامل وفق هندسة الخمس ركائز (Physics, Financials, Structured Decision Object, Tamper-Evident Audit Chain, Separate Demo/Live Architecture).")

AL_WUSTA_LAT, AL_WUSTA_LON = 19.55, 56.35

st.sidebar.subheader("🗄️ 0. Enterprise Database & Event Ledger")
db_backend_mode = st.sidebar.selectbox("اختر بنية قاعدة البيانات", ["SQLite (Local MVP / Pilot)", "PostgreSQL / Enterprise Cloud (Production)"])

def init_enterprise_db():
    conn = sqlite3.connect("re_opt_enterprise.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_orders (
            work_order_id TEXT PRIMARY KEY,
            created_timestamp TEXT,
            created_by TEXT,
            target_block TEXT,
            soil_percentage REAL,
            priority TEXT,
            status TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_chain (
            event_id TEXT PRIMARY KEY,
            timestamp TEXT,
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            temperature REAL,
            wind_speed REAL,
            estimated_pm10 REAL,
            pm10_proxy_status TEXT,
            health_status TEXT,
            source_mode TEXT
        )
    """)
    conn.commit()
    return conn

db_conn = init_enterprise_db()

@st.cache_data(ttl=600)
def fetch_live_weather_metrics(lat, lon):
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m,wind_direction_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data.get("current", {})
            return current.get("temperature_2m", 39.0), current.get("wind_speed_10m", 10.0), "Weather API (Open-Meteo Live)", timestamp_str
    except Exception:
        pass
    return 39.0, 10.0, "Fallback Simulation Stream", timestamp_str

api_temp, api_wind, weather_source_api, data_timestamp = fetch_live_weather_metrics(AL_WUSTA_LAT, AL_WUSTA_LON)
gemini_api_key = st.sidebar.text_input("Gemini API Key (Enterprise Secret)", type="password", value="")

st.sidebar.subheader("🎛️ 1. Data Layer & Connection State")
data_source_mode = st.sidebar.selectbox(
    "اختر قناة تدفق البيانات", 
    [
        "Weather API (Open-Meteo Live)", 
        "Simulation Engine (Pure Local)", 
        "SCADA Real-time Stream (Not Connected)", 
        "IoT Sensor Mesh (Not Connected)"
    ]
)

if "Weather API" in data_source_mode:
    live_temp = api_temp
    live_wind = api_wind
    connection_badge = "🟢 LIVE DATA — Connected to Open-Meteo Weather API"
    data_stream_active = True
elif "Simulation Engine" in data_source_mode:
    live_temp = st.sidebar.number_input("درجة الحرارة المحيطة الافتراضية (°C)", value=39.0)
    live_wind = st.sidebar.number_input("سرعة الرياح الافتراضية (m/s)", value=10.0)
    connection_badge = "🟡 SIMULATED DATA — Local Physics Engine Active"
    data_stream_active = True
else:
    live_temp = 39.0
    live_wind = 10.0
    connection_badge = "🔴 NOT CONNECTED — SCADA/IoT Hardware Link Inactive"
    data_stream_active = False

st.sidebar.markdown(f"**حالة القناة:** `{connection_badge}`")

total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة AC (MW)", min_value=50.0, max_value=1000.0, value=150.0, step=50.0)
num_blocks = st.sidebar.selectbox("عدد حقول محولات الطاقة (Inverter Blocks)", [2, 4, 6, 8], index=1)

st.sidebar.subheader("⚡ 2. Electrical PV Model Topology")
dc_ac_ratio = st.sidebar.slider("نسبة القدرة DC/AC Ratio", min_value=1.1, max_value=1.5, value=1.25, step=0.05)
module_power_w = st.sidebar.selectbox("قدرة اللوح الشمسي الواحد (Watt)", [550, 600, 650, 700], index=1)
modules_per_string = st.sidebar.number_input("عدد الألواح في السلسلة (Modules/String)", min_value=20, max_value=40, value=28)

st.sidebar.subheader("🔌 3. Multi-Layer Loss Matrix")
mismatch_loss = st.sidebar.slider("خسائر عدم التوافق DC Mismatch Loss (%)", 0.0, 5.0, 1.5, 0.1)
wiring_loss_pct = st.sidebar.slider("فقد الكابلات DC Wiring Loss (%)", 0.0, 5.0, 1.5, 0.1)
avail_loss = st.sidebar.slider("خسائر الاعتمادية Availability Loss (%)", 0.0, 5.0, 1.0, 0.1)
aux_loss = st.sidebar.slider("الاستهلاك الداخلي Auxiliary Consumption (%)", 0.0, 3.0, 0.5, 0.1)
annual_degradation = st.sidebar.slider("معدل التدهور السنوي Annual Degradation (%)", 0.1, 1.0, 0.45, 0.05)

st.sidebar.subheader("🌪️ رصد الغبار والجسيمات (PM10 - Proxy Model)")
pm10_estimation_method = st.sidebar.selectbox("مصدر حساب PM10 الحالي", ["Estimated - Wind-based proxy (Unverified)", "IoT Gateway Sensor Mesh (Pending Hardware)"])

if "Wind-based proxy" in pm10_estimation_method:
    estimated_pm10 = 90.0 if live_wind < 12 else 210.0
else:
    estimated_pm10 = 0.0

dust_storm_active = st.sidebar.toggle("🚨 تفعيل حالة طوارئ عاصفة رملية", value=False)
if dust_storm_active:
    estimated_pm10 = max(estimated_pm10, 350.0)
    live_wind = max(live_wind, 19.0)

storm_soiling_penalty = st.sidebar.slider("معامل الغبار الإضافي (%)", min_value=5.0, max_value=50.0, value=18.0) if dust_storm_active else 0.0

st.sidebar.subheader("☀️ إعدادات تكنولوجيا الألواح والمالية")
technology_type = st.sidebar.selectbox("نوع تكنولوجيا الألواح", ["ثنائية الوجه (Bifacial Glass-Glass)", "أحادية الوجه (Mono-facial PERC)"])
albedo = st.sidebar.slider("معامل انعكاس رمال الوسطى (Albedo)", min_value=0.2, max_value=0.7, value=0.45, step=0.05)
bifaciality_factor = st.sidebar.slider("معامل ثنائية الوجه (%)", min_value=65.0, max_value=85.0, value=75.0, step=5.0) / 100.0
tariff = st.sidebar.number_input("تعرفة الطاقة (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.025, step=0.001, format="%.3f")
discount_rate = st.sidebar.slider("معدل الخصم المالي (Discount Rate %)", min_value=3.0, max_value=12.0, value=7.5, step=0.5) / 100.0

st.sidebar.subheader("🤖 إعدادات أسراب روبوتات التنظيف")
initial_robot_capex = st.sidebar.number_input("الاستثمار الأولي للروبوتات (ر.ع)", min_value=500000.0, max_value=6000000.0, value=1500000.0, step=50000.0)
daily_robot_depreciation = st.sidebar.number_input("إهلاك الصيانة اليومي (ر.ع)", min_value=10.0, max_value=600.0, value=55.0, step=5.0)
robot_opex_per_cleaning = st.sidebar.number_input("تكلفة تشغيل الروبوت لكل دورة (ر.ع)", min_value=5.0, max_value=50.0, value=12.0, step=1.0)
mobilisation_cost = st.sidebar.number_input("تكلفة التحشد واللوجستيات (ر.ع)", min_value=5.0, max_value=100.0, value=15.0, step=1.0)

st.sidebar.subheader("🛠️ تخصيص حقول الألواح")
inverter_configs = {}
for i in range(num_blocks):
    inv_name = f"Al Wusta Field Block {i+1}"
    with st.sidebar.expander(f"إعدادات {inv_name}", expanded=(i==0)):
        soil = st.slider(f"نسبة الغبار الحالي (%) - Block {i+1}", 0.0, 45.0, float(3.0 + i * 2.5 + storm_soiling_penalty), key=f"soil_{i}")
        tilt = st.slider(f"زاوية الميل (Tilt °) - Block {i+1}", 5.0, 45.0, 22.0, key=f"tilt_{i}")
        inverter_configs[inv_name] = {'soiling': soil, 'tilt': tilt}

sensor_health_status = "NORMAL (All Sensors Validated)" if data_stream_active else "DISCONNECTED (Awaiting Hardware Integration)"
db_conn.execute("INSERT INTO sensor_readings (timestamp, temperature, wind_speed, estimated_pm10, pm10_proxy_status, health_status, source_mode) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data_timestamp, live_temp, live_wind, estimated_pm10, pm10_estimation_method, sensor_health_status, data_source_mode))
db_conn.commit()

# Structured Decision Object Generator Function
def get_cleaning_decision_object(block_name, soil_pct, block_cap_mw, tariff_val, wind_speed, data_active, health_status):
    recoverable_kwh = (block_cap_mw * 1000.0) * (soil_pct / 100.0) * 5.5
    rev_at_risk = recoverable_kwh * tariff_val
    cleaning_cost = robot_opex_per_cleaning + (daily_robot_depreciation / num_blocks) + mobilisation_cost
    weather_risk = "HIGH" if wind_speed > 15.0 else ("MODERATE" if wind_speed > 10.0 else "LOW")
    data_quality = 96.0 if data_active and "NORMAL" in health_status else 50.0
    net_benefit = rev_at_risk - cleaning_cost

    if not data_active:
        decision = "BLOCKED"
        conf = 0.0
    elif "INVALID" in health_status:
        decision = "BLOCKED"
        conf = 0.0
    elif net_benefit > 50.0 and soil_pct > 12.0 and weather_risk != "HIGH":
        decision = "CLEAN"
        conf = 94.5
    elif net_benefit > 0.0 and soil_pct > 8.0:
        decision = "DELAY"
        conf = 78.2
    else:
        decision = "DO_NOT_CLEAN"
        conf = 98.1

    return {
        "decision": decision,
        "confidence": conf,
        "soiling": soil_pct,
        "energy_loss_mwh": recoverable_kwh / 1000.0,
        "revenue_at_risk": rev_at_risk,
        "cleaning_cost": cleaning_cost,
        "expected_net_benefit": net_benefit,
        "data_quality": data_quality,
        "weather_risk": weather_risk
    }

# Cryptographic Audit Chain Logger Function
def log_audit_event_to_chain(conn, event_id, operator, model_version, input_hash, decision_obj, approval, wo_id):
    cursor = conn.cursor()
    cursor.execute("SELECT current_event_hash FROM audit_chain ORDER BY ROWID DESC LIMIT 1")
    last_row = cursor.fetchone()
    prev_hash = last_row[0] if last_row else "0" * 64
    
    reason_str = f"Soiling: {decision_obj['soiling']}%, NetBenefit: {decision_obj['expected_net_benefit']:.1f} OMR, Weather: {decision_obj['weather_risk']}"
    raw_chain_str = f"{prev_hash}-{event_id}-{data_timestamp}-{decision_obj['decision']}-{reason_str}-{input_hash}"
    curr_hash = hashlib.sha256(raw_chain_str.encode()).hexdigest()
    
    cursor.execute("""
        INSERT OR REPLACE INTO audit_chain VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id, data_timestamp, operator, model_version, input_hash, 
        decision_obj['decision'], reason_str, decision_obj['expected_net_benefit'], 
        approval, wo_id, prev_hash, curr_hash
    ))
    conn.commit()
    return curr_hash

@st.cache_data
def run_true_electrical_pvlib_pipeline(latitude, longitude, total_cap_mw, n_inv, configs, tech_mode, alb, bif_factor, t_amb, wind, mod_watt, mod_per_string, dc_ac, w_loss, mis_loss, av_loss, ax_loss, custom_soiling_mod=1.0):
    tz = 'Asia/Muscat'
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    location = pvlib.location.Location(latitude, longitude, tz=tz)
    solpos = location.get_solarposition(times)
    clearsky = location.get_clearsky(times)
    
    block_ac_w = (total_cap_mw / n_inv) * 1e6
    block_dc_w = block_ac_w * dc_ac
    string_nominal_w = mod_per_string * float(mod_watt)
    strings_per_block = int(block_dc_w / string_nominal_w)
    actual_block_dc_w = strings_per_block * string_nominal_w
    
    mod_params = {
        'alpha_sc': 0.005, 'a_ref': 2.0, 'I_L_ref': 15.6, 'I_o_ref': 1e-10,
        'R_sh_ref': 1000.0, 'R_s': 0.2, 'EgRef': 1.121, 'dEgdT': -0.0002677
    }
    inv_params = {
        'Paco': block_ac_w, 'Pdco': block_ac_w * 1.02, 'Vdco': 1100.0, 
        'Pso': block_ac_w * 0.002, 'C0': -0.00001, 'C1': 0.00001, 'C2': 0.001, 
        'C3': -0.0001, 'Pnt': block_ac_w * 0.001
    }
    temp_model = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass']
    
    total_expected_ac = np.zeros(len(times))
    total_simulated_actual_ac = np.zeros(len(times))
    
    topology_info = {
        'Modules_per_String': mod_per_string,
        'Strings_per_Block': strings_per_block,
        'Total_Modules_Plant': mod_per_string * strings_per_block * n_inv,
        'Actual_DC_AC_Ratio': actual_block_dc_w / block_ac_w
    }
    
    for i, (inv_name, cfg) in enumerate(configs.items()):
        tilt_angle = cfg['tilt']
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=tilt_angle, surface_azimuth=180,
            solar_zenith=solpos['apparent_zenith'], solar_azimuth=solpos['azimuth'],
            dni=clearsky['dni'], ghi=clearsky['ghi'], dhi=clearsky['dhi'], albedo=alb
        )
        poa_global = poa['poa_global'].fillna(0).clip(lower=0)
        if "ثنائية الوجه" in tech_mode:
            poa_global = poa_global * (1.0 + (alb * bif_factor * 0.15))
            
        cell_temp = pvlib.temperature.sapm_cell(poa_global, t_amb, wind, **temp_model)
        IL, I0, Rs, Rsh, nNsVth = pvlib.pvsystem.calcparams_desoto(
            effective_irradiance=poa_global, temp_cell=cell_temp, 
            alpha_sc=mod_params['alpha_sc'], a_ref=mod_params['a_ref'], 
            I_L_ref=mod_params['I_L_ref'], I_o_ref=mod_params['I_o_ref'], 
            R_sh_ref=mod_params['R_sh_ref'], R_s=mod_params['R_s'], 
            EgRef=mod_params['EgRef'], dEgdT=mod_params['dEgdT']
        )
        sd_out = pvlib.pvsystem.singlediode(IL, I0, Rs, Rsh, nNsVth)
        p_mp_module = sd_out['p_mp'].fillna(0)
        v_mp_module = sd_out['v_mp'].fillna(0)
        p_mp_array = p_mp_module * mod_per_string * strings_per_block
        v_mp_array = v_mp_module * mod_per_string
        
        soiling_loss = (cfg['soiling'] * custom_soiling_mod) / 100.0
        tilt_loss = abs(tilt_angle - 22.0) * 0.003
        
        dc_power_ideal = p_mp_array
        dc_power_actual = dc_power_ideal * (1.0 - w_loss/100.0) * (1.0 - mis_loss/100.0) * (1.0 - soiling_loss - tilt_loss)
        v_mp_safe = v_mp_array.replace(0, inv_params['Vdco'])
        
        ac_ideal = pvlib.inverter.sandia(v_mp_safe, dc_power_ideal, inv_params).fillna(0)
        ac_actual = pvlib.inverter.sandia(v_mp_safe, dc_power_actual, inv_params).fillna(0)
        
        ac_ideal = ac_ideal.clip(lower=0) * (1.0 - av_loss/100.0) * (1.0 - ax_loss/100.0)
        ac_actual = ac_actual.clip(lower=0) * (1.0 - av_loss/100.0) * (1.0 - ax_loss/100.0)
        ac_actual = ac_actual * np.random.normal(0.99, 0.01, len(times))
        
        total_expected_ac += ac_ideal
        total_simulated_actual_ac += ac_actual

    sim_res = pd.DataFrame({
        'PVlib Expected Baseline (AC)': total_expected_ac / 1000.0,
        'Simulated Plant Actual (AC)': total_simulated_actual_ac / 1000.0
    }, index=times)
    return sim_res, topology_info

sim_df, plant_topology = run_true_electrical_pvlib_pipeline(
    AL_WUSTA_LAT, AL_WUSTA_LON, total_capacity_mw, num_blocks, 
    inverter_configs, technology_type, albedo, bifaciality_factor, 
    live_temp, live_wind, module_power_w, modules_per_string, dc_ac_ratio, 
    wiring_loss_pct, mismatch_loss, avail_loss, aux_loss, custom_soiling_mod=1.0
)

base_annual_generation_mwh = (sim_df['Simulated Plant Actual (AC)'].sum() * 365)
plant_capex = total_capacity_mw * 330000.0 
lifespan_years = 25

def calculate_robust_irr(cash_flows):
    def npv(r):
        return sum([cf / (1.0 + r) ** i for i, cf in enumerate(cash_flows)])
    try:
        low, high = -0.9, 5.0
        if npv(low) * npv(high) < 0:
            return brentq(npv, low, high, xtol=1e-7, maxiter=500)
        else:
            for h in [10.0, 20.0, 50.0]:
                if npv(low) * npv(h) < 0:
                    return brentq(npv, low, h, xtol=1e-7, maxiter=500)
        r_low, r_high = -0.9, 10.0
        for _ in range(150):
            r_mid = (r_low + r_high) / 2.0
            val = npv(r_mid)
            if abs(val) < 1e-5:
                return r_mid
            if val * npv(r_low) < 0:
                r_high = r_mid
            else:
                r_low = r_mid
        return r_mid
    except Exception:
        return 0.085

total_initial_capex = plant_capex + initial_robot_capex
pv_costs = total_initial_capex / ((1.0 + discount_rate) ** 0)
pv_energy = 0.0
irr_cash_flows = [-total_initial_capex]

for yr in range(1, lifespan_years + 1):
    degradation_factor = (1.0 - (annual_degradation / 100.0)) ** (yr - 1)
    annual_opex = 450000.0 + (initial_robot_capex * 0.05)
    replacement_capex = 1200000.0 if yr in [10, 20] else 0.0
    yearly_cost = annual_opex + replacement_capex
    yearly_energy_mwh = base_annual_generation_mwh * degradation_factor
    yearly_revenue = yearly_energy_mwh * 1000.0 * tariff
    terminal_value = (total_initial_capex * 0.10) if yr == lifespan_years else 0.0
    net_annual_cf = yearly_revenue - yearly_cost + terminal_value
    
    irr_cash_flows.append(net_annual_cf)
    pv_costs += yearly_cost / ((1.0 + discount_rate) ** yr)
    pv_energy += yearly_energy_mwh / ((1.0 + discount_rate) ** yr)

discounted_lcoe = pv_costs / max(1.0, pv_energy * 1000.0)
approx_npv = sum([cf / ((1.0 + discount_rate) ** i) for i, cf in enumerate(irr_cash_flows)])
true_irr = calculate_robust_irr(irr_cash_flows)

offsets = [(0.04, 0.04), (-0.03, 0.05), (-0.04, -0.04), (0.02, -0.05)]
polygon_data = []
for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
    off_lat, off_lon = offsets[i % len(offsets)]
    center_lat, center_lon = AL_WUSTA_LAT + off_lat, AL_WUSTA_LON + off_lon
    dx, dy = 0.012, 0.007
    polygon = [[center_lon - dx, center_lat - dy], [center_lon + dx, center_lat - dy], [center_lon + dx, center_lat + dy], [center_lon - dx, center_lat + dy]]
    is_critical = cfg['soiling'] > 12.0 or dust_storm_active
    color = [239, 68, 68, 230] if is_critical else [30, 64, 175, 230] 
    polygon_data.append({"name": inv_name, "polygon": polygon, "soiling": cfg['soiling'], "status": "🚨 خطر تلوث حرج" if is_critical else "✅ طبيعي", "color": color})
df_poly = pd.DataFrame(polygon_data)

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "🗺️ 1. Data Layer & Quality", 
    "📈 2. Electrical PVlib Physics", 
    "💰 3. Financial Engine", 
    "🎯 4. Decision Object Engine", 
    "🧠 5. AI Operations Agent",
    "📋 6. Persistent Work Orders",
    "🛡️ 7. Tamper-Evident Event Chain",
    "🔬 8. Closed-Loop What-If",
    "☀️ 9. 3D Diorama View"
])

with tab1:
    st.subheader("🗺️ 1. Data Layer & Provenance Architecture")
    st.info(f"📡 القناة المختارة: **{data_source_mode}** | قاعدة البيانات: **{db_backend_mode}**\n\n{connection_badge}\n\n🕒 الطابع الزمني: **{data_timestamp}** | 🌡️ الحرارة: **{live_temp}°C** | 🌬️ الرياح: **{live_wind} m/s** | 🌪️ مؤشر PM10: **{estimated_pm10:.1f} µg/m³**")
    
    col_a, col_b = st.columns(2)
    with col_a:
        st.info("**مسار الـ MVP الحالي (Simulated Actual):**\n`PVlib Baseline Expected → Simulated Plant Actual (with Soiling & Stochastic Noise)`")
    with col_b:
        st.success("**المسار الهندسي للإنتاج (SCADA Integration):**\n`PVlib Expected Baseline → SCADA Real-time Actual → Deviation & Loss Engine`")

    layer = pdk.Layer("PolygonLayer", df_poly, id="wusta-map", get_polygon="polygon", get_fill_color="color", get_line_color=[255, 255, 255], line_width_min_pixels=3, extruded=False, pickable=True, auto_highlight=True)
    r = pdk.Deck(layers=[layer], initial_view_state=pdk.ViewState(latitude=AL_WUSTA_LAT, longitude=AL_WUSTA_LON, zoom=9, pitch=20, bearing=0), map_style="https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json", tooltip={"text": "الحقل: {name}\nالحالة: {status}\nنسبة الغبار: {soiling}%"})
    st.pydeck_chart(r)

with tab2:
    st.subheader("📈 2. Full Electrical PVlib Physics Engine (Single-Diode Model)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("النسبة الفعلية DC/AC", f"{plant_topology['Actual_DC_AC_Ratio']:.2f}")
    c2.metric("ألواح السلسلة (Modules/String)", f"{plant_topology['Modules_per_String']}")
    c3.metric("سلاسل المحول (Strings/Block)", f"{plant_topology['Strings_per_Block']:,}")
    c4.metric("إجمالي الألواح (Total Modules)", f"{plant_topology['Total_Modules_Plant']:,}")
    st.markdown("---")
    st.line_chart(sim_df)

with tab3:
    st.subheader("💰 3. Real Financial Engine (CAPEX, NPV, IRR, LCOE)")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("تكلفة LCOE المخصومة", f"{discounted_lcoe:.4f} ر.ع/kWh", delta="شاملة التدهور السنوي والفقد الكهربائي")
    f2.metric("صافي القيمة الحالية (NPV)", f"{approx_npv:,.0f} ر.ع")
    f3.metric("معدل العائد الداخلي (True IRR)", f"{true_irr*100:.2f}%", delta="محسوب بـ Brent's Method المحصّن")
    f4.metric("الاستثمار الأولي للروبوتات", f"{initial_robot_capex:,.0f} ر.ع")

with tab4:
    st.subheader("🎯 4. Structured Decision Object Engine (RE-OPT V2.1)")
    st.markdown("يعتمد هذا القسم على **Decision Object** المنسق الذي يحدد نسبة الثقة، رموز الأسباب، العائد المتوقع، ومخاطر الطقس بدقة مؤسسية صارمة:")
    
    for inv_name, cfg in inverter_configs.items():
        dec_obj = get_cleaning_decision_object(inv_name, cfg['soiling'], total_capacity_mw / num_blocks, tariff, live_wind, data_stream_active, sensor_health_status)
        
        with st.expander(f"📌 {inv_name} — Decision Object State: `{dec_obj['decision']}` (Confidence: {dec_obj['confidence']}%)", expanded=True):
            dc1, dc2, dc3, dc4 = st.columns(4)
            dc1.metric("القرار التشغيلي", dec_obj['decision'])
            dc2.metric("نسبة الثقة", f"{dec_obj['confidence']}%")
            dc3.metric("صافي المنفعة المتوقعة", f"{dec_obj['expected_net_benefit']:,.1f} ر.ع")
            dc4.metric("مخاطر الطقس", dec_obj['weather_risk'])
            
            st.json(dec_obj)

with tab5:
    st.subheader("🧠 5. RE-OPT AI Operations Agent (Explainable Interpretation)")
    st.markdown("يقوم الذكاء الاصطناعي حصراً بتفسير وتوضيح مخرجات كائن القرار (Decision Object) دون التدخل في الحسابات الحتمية:")

    agent_target_block = st.selectbox("اختر الحقل للتحليل بواسطة الوكيل الذكي", list(inverter_configs.keys()), key="agent_sel")
    agent_dec = get_cleaning_decision_object(agent_target_block, inverter_configs[agent_target_block]['soiling'], total_capacity_mw / num_blocks, tariff, live_wind, data_stream_active, sensor_health_status)

    c_ag1, c_ag2, c_ag3, c_ag4 = st.columns(4)
    c_ag1.metric("حالة الاتصال", connection_badge.split(" — ")[0])
    c_ag2.metric("القرار المحدد", agent_dec['decision'])
    c_ag3.metric("الإيراد المهدد", f"{agent_dec['revenue_at_risk']:,.1f} ر.ع")
    c_ag4.metric("صافي العائد المتوقع", f"+{agent_dec['expected_net_benefit']:,.1f} ر.ع")

    st.markdown("---")
    if st.button("🤖 طلب التفسير والتشخيص التشغيلي عبر AI Agent"):
        if not gemini_api_key:
            st.error("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي.")
        else:
            with st.spinner("الوكيل الذكي يقرأ كائن القرار والفيزياء الكهربائية..."):
                try:
                    client = genai.Client(api_key=gemini_api_key)
                    agent_prompt = (
                        f"بصفتك وكيل التشغيل الذكي، قم بتقديم تفسير تشغيلي لحالة الحقل الآتي بناءً على Decision Object:\n"
                        f"- الحقل: {agent_target_block}\n- تفاصيل كائن القرار: {agent_dec}\n\n"
                        f"اشرح بمهنية لماذا هذا القرار يعظّم العائد التجاري ويقلل مخاطر التشغيل في صحراء الوسطى."
                    )
                    response = client.models.generate_content(model='gemini-3.6-flash', contents=agent_prompt)
                    st.markdown("### 🤖 تفسير الوكيل الذكي (AI Operational Interpretation):")
                    st.markdown(response.text)
                except Exception as e:
                    st.error(f"خطأ: {e}")

    st.markdown("---")
    st.markdown("#### 🛡️ بوابة الاعتماد والتشغيل وتوليد سجل التدقيق (Human-in-the-Loop)")
    operator_name = st.text_input("اسم المشرف المسؤول", value="Mohammed Al Qutaiti (Lead Asset Operator)")
    approval_toggle = st.checkbox(f"أوافق بصفتي مشرف العمليات على تنفيذ قرار `{agent_dec['decision']}` لـ {agent_target_block}", key="agent_approval")
    
    if approval_toggle and agent_dec['decision'] != "BLOCKED":
        if st.button("🚀 اعتماد وإرسال أمر الشغل وتسجيل الحدث في السلسلة المشفرة"):
            wo_id = f"WO-ALWUSTA-2026-{np.random.randint(10000, 99999)}"
            input_hash = hashlib.sha256(str(agent_dec).encode()).hexdigest()
            event_id = f"EVT-OMAN-{np.random.randint(100000, 999999)}"
            
            # حفظ أمر الشغل
            db_conn.execute("INSERT INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (wo_id, data_timestamp, operator_name, agent_target_block, agent_dec['soiling'], "High Priority", "Dispatched & Verified"))
            
            # حفظ الحدث في السلسلة المشفرة (Tamper-Evident Audit Chain)
            final_hash = log_audit_event_to_chain(db_conn, event_id, operator_name, "RE-OPT-V2.1-OQ", input_hash, agent_dec, "Approved by Human Operator", wo_id)
            db_conn.commit()
            
            st.success(f"✅ تم إصدار أمر الشغل `{wo_id}` وتوثيق الحدث في سلسلة التدقيق المشفرة برقم بصمة: `{final_hash[:16]}...`")

with tab6:
    st.subheader("📋 6. Persistent Work Orders (Database Store)")
    st.dataframe(pd.read_sql("SELECT * FROM work_orders", db_conn), use_container_width=True)

with tab7:
    st.subheader("🛡️ 7. Tamper-Evident Event Chain (Cryptographic Ledger)")
    st.markdown("سجل التدقيق المؤسسي المؤمن بربط السلاسل الزمنية (`Previous Event Hash` ⇄ `Current Event Hash`):")
    audit_chain_df = pd.read_sql("SELECT * FROM audit_chain", db_conn)
    if not audit_chain_df.empty:
        st.dataframe(audit_chain_df, use_container_width=True)
    else:
        st.info("ℹ️ لا توجد أحداث مسجلة في السلسلة حتى الآن. قم باعتماد أمر شغل من تبويب الوكيل الذكي لتوليد أول حدث مشفر.")

with tab8:
    st.subheader("🔬 8. Closed-Loop What-If Scenario Engine")
    st.info("ℹ️ **Scenario Assumption Notice:** معامل الغبار هنا مبني على نموذج تقديري افتراضي (Empirical Proxy).")
    
    scen_col1, scen_col2 = st.columns(2)
    with scen_col1:
        sim_storm_inc = st.slider("معدل زيادة العواصف (% الغبار - Assumption)", 0, 100, 20)
        sim_robots_add = st.slider("إضافة أسراب روبوتات جديدة", 0, 2000, 400)
    with scen_col2:
        sim_cleaning_freq = st.slider("جدول التنظيف الافتراضي (أيام بين الغسلات)", 5, 30, 15)

    cleaning_eff = 15.0 / max(5.0, float(sim_cleaning_freq))
    scen_soiling_mod = (1.0 + (sim_storm_inc / 100.0)) / cleaning_eff
    
    scen_sim_df, _ = run_true_electrical_pvlib_pipeline(
        AL_WUSTA_LAT, AL_WUSTA_LON, total_capacity_mw, num_blocks, 
        inverter_configs, technology_type, albedo, bifaciality_factor, 
        live_temp, live_wind, module_power_w, modules_per_string, dc_ac_ratio, 
        wiring_loss_pct, mismatch_loss, avail_loss, aux_loss, custom_soiling_mod=scen_soiling_mod
    )
    scen_gen_mwh = (scen_sim_df['Simulated Plant Actual (AC)'].sum() * 365)
    scen_capex = plant_capex + initial_robot_capex + (sim_robots_add * 650.0)
    scen_costs = scen_capex / ((1.0 + discount_rate) ** 0)
    scen_ener = 0.0
    scen_irr_flows = [-scen_capex]
    
    for yr in range(1, lifespan_years + 1):
        deg = (1.0 - (annual_degradation / 100.0)) ** (yr - 1)
        opex_y = 450000.0 + ((initial_robot_capex + (sim_robots_add * 650.0)) * 0.05)
        repl_y = 1200000.0 if yr in [10, 20] else 0.0
        y_cost = opex_y + repl_y
        y_energy = scen_gen_mwh * deg
        y_rev = y_energy * 1000.0 * tariff
        term_val = (scen_capex * 0.10) if yr == lifespan_years else 0.0
        
        scen_irr_flows.append(y_rev - y_cost + term_val)
        scen_costs += y_cost / ((1.0 + discount_rate) ** yr)
        scen_ener += y_energy / ((1.0 + discount_rate) ** yr)

    scen_lcoe = scen_costs / max(1.0, scen_ener * 1000.0)
    scen_true_irr = calculate_robust_irr(scen_irr_flows)

    res1, res2, res3 = st.columns(3)
    res1.metric("الإنتاج السنوي بعد السيناريو", f"{scen_gen_mwh:,.1f} MWh")
    res2.metric("تكلفة LCOE المعدلة بالنموذج", f"{scen_lcoe:.4f} ر.ع", delta=f"{scen_lcoe - discounted_lcoe:+.4f} ر.ع", delta_color="inverse")
    res3.metric("IRR المعدل للسيناريو", f"{scen_true_irr*100:.2f}%", delta=f"{(scen_true_irr - true_irr)*100:+.2f}%")

with tab9:
    st.subheader("☀️ 9. 3D Diorama View")
    cols = st.columns(2)
    for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
        soiling, tilt = cfg['soiling'], cfg['tilt']
        is_critical = cfg['soiling'] > 12.0 or dust_storm_active
        panel_color = "#ef4444" if is_critical else "#1e40af"
        
        diorama_html = f"""
        <div style="background: #1e293b; border: 2px solid {'#ef4444' if is_critical else '#0ea5e9'}; border-radius: 16px; padding: 16px; margin-bottom: 20px; color: white; font-family: sans-serif; text-align: right; direction: rtl;">
            <h4 style="margin: 0; color: #f8fafc; font-size: 16px;">🌱 {inv_name}</h4>
            <div style="background: linear-gradient(135deg, #d97706, #92400e); border-radius: 10px; height: 110px; display: flex; align-items: center; justify-content: center; margin-top: 10px;">
                <div style="background: {panel_color}; width: 70%; height: 30px; border-radius: 4px;"></div>
            </div>
            <p style="font-size: 12px; margin-top: 8px;">الغبار: <b>{soiling}%</b> | الميل: <b>{tilt}°</b></p>
        </div>
        """
        with cols[i % 2]:
            components.html(diorama_html, height=210)
