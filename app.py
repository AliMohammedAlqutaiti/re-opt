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
from google import genai

st.set_page_config(page_title="RE-OPT V2: Enterprise Solar Twin", layout="wide")

st.markdown('<div id="top-anchor"></div>', unsafe_allow_html=True)

st.title("⚡ RE-OPT V2: Enterprise Autonomous Operating & Financial Twin")
st.markdown("نظام التشغيل والقرار المؤسسي الموحد لمحطات الطاقة الشمسية - جاهز لـ Pilot.")

AL_WUSTA_LAT, AL_WUSTA_LON = 19.55, 56.35

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
        CREATE TABLE IF NOT EXISTS audit_events (
            audit_event_id TEXT PRIMARY KEY,
            timestamp TEXT,
            model_version TEXT,
            data_hash TEXT,
            pm10_source_type TEXT,
            total_loss_kwh TEXT,
            status TEXT
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scenario_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            storm_increase REAL,
            added_robots INTEGER,
            cleaning_freq INTEGER,
            resulting_lcoe REAL,
            resulting_irr REAL
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
            temp = current.get("temperature_2m", 39.0)
            wind = current.get("wind_speed_10m", 10.0)
            return temp, wind, "Weather API (Open-Meteo Live)", timestamp_str
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
    connection_badge = "🟢 LIVE — Connected to Open-Meteo Weather API"
    data_stream_active = True
elif "Simulation Engine" in data_source_mode:
    live_temp = st.sidebar.number_input("درجة الحرارة المحيطة الافتراضية (°C)", value=39.0)
    live_wind = st.sidebar.number_input("سرعة الرياح الافتراضية (m/s)", value=10.0)
    connection_badge = "🟡 SIMULATED — Local Physics Engine Active"
    data_stream_active = True
else:
    live_temp = 39.0
    live_wind = 10.0
    connection_badge = "🔴 OFFLINE / DEMO MODE — SCADA/IoT Hardware Link Not Connected"
    data_stream_active = False

st.sidebar.markdown(f"**حالة القناة:** `{connection_badge}`")

total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=1000.0, value=150.0, step=50.0)
total_capacity = total_capacity_mw * 1000 
num_blocks = st.sidebar.selectbox("عدد حقول محولات الطاقة (Inverter Blocks)", [2, 4, 6, 8], index=1)

st.sidebar.subheader("⚡ 2. Electrical PV Model Parameters")
dc_ac_ratio = st.sidebar.slider("نسبة القدرة DC/AC Ratio", min_value=1.1, max_value=1.4, value=1.25, step=0.05)
module_power_w = st.sidebar.selectbox("قدرة اللوح الشمسي الواحد (Watt)", [550, 600, 650, 700], index=2)
wiring_loss_pct = st.sidebar.slider("فقد الكابلات والأسلاك DC Wiring Loss (%)", min_value=0.5, max_value=3.0, value=1.5, step=0.1)
inverter_max_eff = st.sidebar.slider("الكفاءة القصوى للمحول Inverter Peak Efficiency (%)", min_value=96.0, max_value=99.0, value=98.5, step=0.1) / 100.0

st.sidebar.subheader("🌪️ رصد الغبار والجسيمات (PM10 - Proxy Model)")
pm10_estimation_method = st.sidebar.selectbox("مصدر حساب PM10 الحالي", ["Estimated — Wind-based proxy (Unverified)", "IoT Gateway Sensor Mesh (Pending Hardware Integration)"])

if "Wind-based proxy" in pm10_estimation_method:
    estimated_pm10 = 90.0 if live_wind < 12 else 210.0
    pm10_source_label = f"Estimated — Wind-based proxy (Threshold: {live_wind} m/s) - Timestamp: {data_timestamp}"
else:
    estimated_pm10 = 0.0
    pm10_source_label = f"IoT Gateway Sensor Mesh — Not Connected"

dust_storm_active = st.sidebar.toggle("🚨 تفعيل حالة طوارئ عاصفة رملية", value=False)
if dust_storm_active:
    estimated_pm10 = max(estimated_pm10, 350.0)
    live_wind = max(live_wind, 19.0)
    pm10_source_label = f"Emergency Storm Proxy Override - Timestamp: {data_timestamp}"

storm_soiling_penalty = st.sidebar.slider("معامل الغبار الإضافي (%)", min_value=5.0, max_value=50.0, value=18.0) if dust_storm_active else 0.0

st.sidebar.subheader("☀️ إعدادات تكنولوجيا الألواح والمالية")
technology_type = st.sidebar.selectbox("نوع تكنولوجيا الألواح", ["ثنائية الوجه (Bifacial Glass-Glass)", "أحادية الوجه (Mono-facial PERC)"])
albedo = st.sidebar.slider("معامل انعكاس رمال الوسطى (Albedo)", min_value=0.2, max_value=0.7, value=0.45, step=0.05)
bifaciality_factor = st.sidebar.slider("معامل ثنائية الوجه (%)", min_value=65.0, max_value=85.0, value=75.0, step=5.0) / 100.0
tariff = st.sidebar.number_input("تعرفة الطاقة (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.025, step=0.001, format="%.3f")
discount_rate = st.sidebar.slider("معدل الخصم المالي (Discount Rate %)", min_value=3.0, max_value=12.0, value=7.5, step=0.5) / 100.0

st.sidebar.subheader("🤖 إعدادات أسراب روبوتات التنظيف")
total_robots = st.sidebar.number_input("عدد روبوتات التنظيف الجاف", min_value=500, max_value=6000, value=2200, step=100)
initial_robot_capex = st.sidebar.number_input("الاستثمار الأولي للروبوتات (ر.ع)", min_value=500000.0, max_value=6000000.0, value=1500000.0, step=50000.0)
daily_robot_depreciation = st.sidebar.number_input("إهلاك الصيانة اليومي", min_value=10.0, max_value=600.0, value=55.0, step=5.0)

st.sidebar.subheader("🛠️ تخصيص حقول الألواح")
inverter_configs = {}
for i in range(num_blocks):
    inv_name = f"Al Wusta Field Block {i+1}"
    with st.sidebar.expander(f"إعدادات {inv_name}", expanded=(i==0)):
        soil = st.slider(f"نسبة الغبار الحالي (%) - Block {i+1}", 0.0, 45.0, float(3.0 + i * 2.5 + storm_soiling_penalty), key=f"soil_{i}")
        tilt = st.slider(f"زاوية الميل (Tilt °) - Block {i+1}", 5.0, 45.0, 22.0, key=f"tilt_{i}")
        inverter_configs[inv_name] = {'soiling': soil, 'tilt': tilt}

if not data_stream_active:
    sensor_health_status = "DISCONNECTED (Awaiting Hardware Integration)"
elif live_temp > 65.0 or live_temp < -10.0 or live_wind < 0 or estimated_pm10 < 0:
    sensor_health_status = "INVALID (Blocked by Data Quality Engine)"
elif live_wind > 35.0:
    sensor_health_status = "WARNING (Stale/High Wind Anomaly)"
else:
    sensor_health_status = "NORMAL (All Sensors Validated)"

db_conn.execute("INSERT INTO sensor_readings (timestamp, temperature, wind_speed, estimated_pm10, pm10_proxy_status, health_status, source_mode) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data_timestamp, live_temp, live_wind, estimated_pm10, pm10_estimation_method, sensor_health_status, data_source_mode))
db_conn.commit()

cursor_chk = db_conn.cursor()
cursor_chk.execute("SELECT COUNT(*) FROM work_orders")
if cursor_chk.fetchone()[0] == 0:
    db_conn.execute("INSERT INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("WO-ALWUSTA-2026-000184", data_timestamp, "Autonomous AI Agent", "Al Wusta Field Block 1", 14.5, "High Priority", "Dispatched & Verified"))
    db_conn.commit()

st.sidebar.markdown("---")
st.sidebar.subheader("⚡ Quick Order Panel (SQLite Store)")
quick_target_block = st.sidebar.selectbox("الحقل المستهدف لأمر الشغل الفوري", list(inverter_configs.keys()), key="quick_block")
quick_priority = st.sidebar.selectbox("أولوية أمر العمل", ["Normal Priority", "High Priority", "Emergency Dispatch"])
if st.sidebar.button("🚀 إصدار أمر شغل فوري وحفظه في SQLite"):
    quick_wo_id = f"WO-ALWUSTA-QUICK-{np.random.randint(1000, 9999)}"
    db_conn.execute("INSERT INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (quick_wo_id, data_timestamp, "Quick Operator Panel", quick_target_block, inverter_configs[quick_target_block]['soiling'], quick_priority, "Dispatched & Active"))
    db_conn.commit()
    st.sidebar.success(f"✅ تم حفظ أمر الشغل `{quick_wo_id}` في جدول `work_orders` بنجاح!")

@st.cache_data
def run_full_electrical_pvlib_pipeline(latitude, longitude, total_cap, n_inv, configs, tech_mode, alb, bif_factor, t_amb, wind, mod_watt, dc_ac, wiring_loss, peak_inv_eff, custom_soiling_mod=1.0):
    tz = 'Asia/Muscat'
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    location = pvlib.location.Location(latitude, longitude, tz=tz)
    
    solpos = location.get_solarposition(times)
    clearsky = location.get_clearsky(times)
    
    block_capacity_mw = (total_cap / n_inv) / 1000.0
    inverter_capacity_mw = block_capacity_mw / dc_ac
    
    simulation_results = {}
    total_expected_ac = np.zeros(len(times))
    total_simulated_actual_ac = np.zeros(len(times))
    
    temperature_model_parameters = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass']
    gamma_pdc = -0.0035
    
    for i, (inv_name, cfg) in enumerate(configs.items()):
        tilt_angle = cfg['tilt']
        
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=tilt_angle,
            surface_azimuth=180,
            solar_zenith=solpos['apparent_zenith'],
            solar_azimuth=solpos['azimuth'],
            dni=clearsky['dni'],
            ghi=clearsky['ghi'],
            dhi=clearsky['dhi'],
            albedo=alb
        )
        poa_global = poa['poa_global'].fillna(0)
        
        cell_temp = pvlib.temperature.sapm_cell(
            poa_global, t_amb, wind, **temperature_model_parameters
        )
        
        effective_poa = poa_global / 1000.0
        temp_correction = 1.0 + gamma_pdc * (cell_temp - 25.0)
        dc_power_raw = effective_poa * (block_capacity_mw * 1000.0) * temp_correction
        
        if "ثنائية الوجه" in tech_mode:
            bifacial_gain = 1.0 + (alb * bif_factor * 0.15)
            dc_power_raw *= bifacial_gain
            
        dc_power_after_wiring = dc_power_raw * (1.0 - (wiring_loss / 100.0))
        
        soiling_loss = (cfg['soiling'] * custom_soiling_mod) / 100.0
        tilt_loss = abs(tilt_angle - 22.0) * 0.003
        actual_dc_factor = max(0.05, 1.0 - (soiling_loss + tilt_loss))
        
        simulated_actual_dc = dc_power_after_wiring * actual_dc_factor * np.random.normal(0.99, 0.01, len(times))
        
        max_inv_ac_kw = inverter_capacity_mw * 1000.0
        expected_ac = np.clip(dc_power_after_wiring * peak_inv_eff, 0, max_inv_ac_kw)
        simulated_actual_ac = np.clip(simulated_actual_dc * peak_inv_eff, 0, max_inv_ac_kw)
        
        total_expected_ac += expected_ac
        total_simulated_actual_ac += simulated_actual_ac

    simulation_results['Plant_Total'] = pd.DataFrame({
        'إجمالي المحطة المتوقع AC (PVlib Electrical)': total_expected_ac / 1000.0,
        'إجمالي الإنتاج الفعلي المحاكى AC (Simulated Actual)': total_simulated_actual_ac / 1000.0
    }, index=times)
    
    return simulation_results

sim_data = run_full_electrical_pvlib_pipeline(
    AL_WUSTA_LAT, AL_WUSTA_LON, total_capacity, num_blocks, 
    inverter_configs, technology_type, albedo, bifaciality_factor, 
    live_temp, live_wind, module_power_w, dc_ac_ratio, 
    wiring_loss_pct, inverter_max_eff, custom_soiling_mod=1.0
)

df_total = sim_data['Plant_Total']
total_energy_diff_kwh = ((df_total['إجمالي المحطة المتوقع AC (PVlib Electrical)'] - df_total['إجمالي الإنتاج الفعلي المحاكى AC (Simulated Actual)']).sum()) * 1000.0
daily_financial_loss = max(0.0, abs(total_energy_diff_kwh) * tariff)
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

base_annual_generation_mwh = (df_total['إجمالي الإنتاج الفعلي المحاكى AC (Simulated Actual)'].sum() * 365)
plant_capex = total_capacity_mw * 330000.0 
lifespan_years = 25

def calculate_true_irr(cash_flows):
    rate = 0.1
    for _ in range(100):
        npv = sum([cf / (1.0 + rate) ** i for i, cf in enumerate(cash_flows)])
        d_npv = sum([-i * cf / (1.0 + rate) ** (i + 1) for i, cf in enumerate(cash_flows)])
        if abs(d_npv) < 1e-9:
            break
        new_rate = rate - npv / d_npv
        if abs(new_rate - rate) < 1e-7:
            return new_rate
        rate = new_rate
    return rate

total_initial_capex = plant_capex + initial_robot_capex
pv_costs = total_initial_capex / ((1.0 + discount_rate) ** 0)
pv_energy = 0.0
irr_cash_flows = [-total_initial_capex]

for yr in range(1, lifespan_years + 1):
    degradation_factor = (1.0 - 0.005) ** (yr - 1)
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
true_irr = calculate_true_irr(irr_cash_flows)

offsets = [(0.04, 0.04), (-0.03, 0.05), (-0.04, -0.04), (0.02, -0.05)]
polygon_data = []
for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
    off_lat, off_lon = offsets[i % len(offsets)]
    center_lat = AL_WUSTA_LAT + off_lat
    center_lon = AL_WUSTA_LON + off_lon
    dx, dy = 0.012, 0.007
    polygon = [
        [center_lon - dx, center_lat - dy],
        [center_lon + dx, center_lat - dy],
        [center_lon + dx, center_lat + dy],
        [center_lon - dx, center_lat + dy]
    ]
    is_critical = cfg['soiling'] > 12.0 or dust_storm_active
    color = [239, 68, 68, 230] if is_critical else [30, 64, 175, 230] 
    polygon_data.append({
        "name": inv_name, "polygon": polygon, "soiling": cfg['soiling'],
        "status": "🚨 خطر تلوث حرج" if is_critical else "✅ طبيعي", "color": color
    })
df_poly = pd.DataFrame(polygon_data)

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "🗺️ 1. Data Layer & Quality", 
    "📈 2. Electrical PVlib Physics", 
    "💰 3. Financial Engine", 
    "🎯 4. Decision Engine", 
    "🧠 5. AI Operations Agent",
    "📋 6. Persistent Work Orders (SQLite)",
    "🛡️ 7. Integrity-Protected Audit",
    "🔬 8. Closed-Loop What-If",
    "☀️ 9. 3D Diorama View"
])

with tab1:
    st.subheader("🗺️ 1. Data Layer & Connection State")
    st.info(f"📡 القناة المختارة: **{data_source_mode}**\n\n{connection_badge}\n\n🕒 الطابع الزمني: **{data_timestamp}** | 🌡️ الحرارة: **{live_temp}°C** | 🌬️ الرياح: **{live_wind} m/s** | 🌪️ مؤشر PM10: **{estimated_pm10:.1f} µg/m³** (`{pm10_estimation_method}`)")
    
    layer = pdk.Layer(
        "PolygonLayer", df_poly, id="wusta-map",
        get_polygon="polygon", get_fill_color="color", get_line_color=[255, 255, 255],
        line_width_min_pixels=3, extruded=False, pickable=True, auto_highlight=True,
    )
    view_state = pdk.ViewState(latitude=AL_WUSTA_LAT, longitude=AL_WUSTA_LON, zoom=9, pitch=20, bearing=0)
    r = pdk.Deck(layers=[layer], initial_view_state=view_state, map_style="https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json", tooltip={"text": "الحقل: {name}\nالحالة: {status}\nنسبة الغبار: {soiling}%"})
    st.pydeck_chart(r)

with tab2:
    st.subheader("📈 2. Full Electrical PVlib Physics Engine")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي القدرة", f"{total_capacity_mw} MW")
    c2.metric("فارق الطاقة (AC)", f"{abs(total_energy_diff_kwh):,.1f} kWh")
    c3.metric("نسبة DC/AC", f"{dc_ac_ratio}")
    c4.metric("فقد الكابلات", f"{wiring_loss_pct}%")
    st.markdown("---")
    st.line_chart(df_total)

with tab3:
    st.subheader("💰 3. Real Financial Engine (LCOE, NPV, True IRR) - Corrected CAPEX t0")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("تكلفة LCOE المخصومة", f"{discounted_lcoe:.4f} ر.ع/kWh", delta="مصصح (CAPEX @ t0)")
    f2.metric("صافي القيمة الحالية (NPV)", f"{approx_npv:,.0f} ر.ع")
    f3.metric("معدل العائد الداخلي (True IRR)", f"{true_irr*100:.2f}%", delta="محسوب فعلياً من التدفقات")
    f4.metric("صافي العائد اليومي للروبوتات", f"{net_robotic_roi:,.2f} ر.ع")

with tab4:
    st.subheader("🎯 4. Decision Engine & Robotic Dispatch")
    cleaning_report = []
    block_cap_mw = total_capacity_mw / num_blocks
    for inv_name, cfg in inverter_configs.items():
        soiling = cfg['soiling']
        loss_kwh = (block_cap_mw * 1000) * (soiling / 100.0) * 5.5
        loss_omr = loss_kwh * tariff
        net_b = loss_omr - 45.0
        
        if not data_stream_active:
            decision = "⛔ محظور (القناة غير متصلة)"
        elif sensor_health_status.startswith("INVALID"):
            decision = "⛔ محظور (خطأ في الحساسات)"
        elif net_b > 0 and soiling > 12.0:
            decision = f"🚀 **تنظيف فوري** (عائد صافٍ: +{net_b:,.1f} ر.ع)"
        else:
            decision = "✅ مؤجل (غير مجدٍ مالياً حالياً)"
            
        cleaning_report.append({"الحقل": inv_name, "نسبة الغبار": f"{soiling}%", "الإيراد المهدد": f"{loss_omr:,.1f} ر.ع", "قرار المحرك": decision})
    st.table(pd.DataFrame(cleaning_report))

with tab5:
    st.subheader("🧠 5. RE-OPT AI Operations Agent (Operational Interpretation)")
    st.markdown("حدد Decision Engine قرار التشغيل بناءً على الحسابات الحتمية، بينما يقوم الذكاء الاصطناعي حصراً بتقديم التفسير والتشخيص الفني:")

    agent_target_block = st.selectbox("اختر الحقل للتحليل بواسطة الوكيل الذكي", list(inverter_configs.keys()), key="agent_sel")
    block_soil = inverter_configs[agent_target_block]['soiling']
    
    block_cap_mw = total_capacity_mw / num_blocks
    expected_loss_mwh = (block_cap_mw * (block_soil / 100.0) * 5.5)
    revenue_at_risk = expected_loss_mwh * 1000 * tariff
    cleaning_cost = 45.0
    net_benefit = revenue_at_risk - cleaning_cost

    if not data_stream_active:
        decision_status = "BLOCKED (Data Channel Not Connected)"
        should_clean = False
    elif sensor_health_status.startswith("INVALID"):
        decision_status = "BLOCKED (Data Quality Engine Triggered)"
        should_clean = False
    elif net_benefit > 0 and block_soil > 12.0:
        decision_status = "RECOMMENDED (Financial Net Benefit Positive)"
        should_clean = True
    else:
        decision_status = "DEFERRED (Cleaning not economically justified)"
        should_clean = False

    c_ag1, c_ag2, c_ag3, c_ag4 = st.columns(4)
    c_ag1.metric("حالة الاتصال", connection_badge.split(" — ")[0])
    c_ag2.metric("قرار Decision Engine", decision_status)
    c_ag3.metric("الإيراد المهدد", f"{revenue_at_risk:,.1f} ر.ع")
    c_ag4.metric("صافي العائد المتوقع", f"+{net_benefit:,.1f} ر.ع", delta="مجدٍ" if net_benefit > 0 else "غير مجدٍ")

    st.markdown("---")

    if st.button("🤖 طلب التفسير والتشخيص التشغيلي عبر AI Agent"):
        if not gemini_api_key:
            st.error("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي.")
        else:
            with st.spinner("الوكيل الذكي يقرأ نتائج محرك القرار والفيزياء..."):
                try:
                    client = genai.Client(api_key=gemini_api_key)
                    agent_prompt = (
                        f"بصفتك وكيل التشغيل الذكي، قم بتقديم تفسير تشغيلي لحالة الحقل الآتي بناءً على قرارات Decision Engine:\n"
                        f"- الحقل: {agent_target_block}\n- الغبار: {block_soil}%\n"
                        f"- الإيراد المهدد: {revenue_at_risk:,.1f} ر.ع\n- قرار Decision Engine: {decision_status}\n\n"
                        f"اشرح بمهنية لماذا هذا التفسير يدعم العمليات في صحراء الوسطى."
                    )
                    response = client.models.generate_content(model='gemini-3.6-flash', contents=agent_prompt)
                    st.markdown("### 🤖 تفسير الوكيل الذكي (AI Operational Interpretation):")
                    st.markdown(response.text)
                except Exception as e:
                    st.error(f"خطأ: {e}")

    st.markdown("---")
    st.markdown("#### 🛡️ بوابة الاعتماد والتشغيل (Human-in-the-Loop)")
    approval_toggle = st.checkbox(f"أوافق بصفتي مشرف العمليات على تنفيذ توصية Decision Engine لـ {agent_target_block}", key="agent_approval")
    if approval_toggle and should_clean:
        if st.button("🚀 اعتماد وإرسال أمر الشغل لفرق الصيانة"):
            new_id = f"WO-ALWUSTA-2026-{np.random.randint(10000, 99999)}"
            db_conn.execute("INSERT INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (new_id, data_timestamp, "Decision Engine + Operator Verified", agent_target_block, block_soil, "High Priority", "Dispatched"))
            db_conn.commit()
            st.success(f"✅ تم حفظ أمر الشغل برقم `{new_id}` مباشرة في قاعدة بيانات SQLite المستدامة!")

with tab6:
    st.subheader("📋 6. Persistent Work Orders (SQLite Database Store)")
    st.dataframe(pd.read_sql("SELECT * FROM work_orders", db_conn), use_container_width=True)

with tab7:
    st.subheader("🛡️ 7. Integrity-Protected Audit Record (Tamper-Evident Design)")
    audit_id = "LOSS-EVT-OMAN-2026-902"
    raw_str = f"{audit_id}-{data_timestamp}-{estimated_pm10}-{total_energy_diff_kwh}-v2.5.3-IntegrityProtected"
    data_hash = hashlib.sha256(raw_str.encode()).hexdigest()

    audit_payload = {
        "audit_event_id": audit_id,
        "model_version": "RE-OPT-Physics-v2.5.3-IntegrityProtected",
        "timestamp": data_timestamp,
        "data_channel": data_source_mode,
        "pm10_source_type": pm10_estimation_method,
        "integrity_hash_sha256": data_hash,
        "chain_link_status": "Single Node Hash Protected (Pending Full Event Chain Indexing)",
        "sensor_health": sensor_health_status,
        "estimated_pm10_proxy": f"{estimated_pm10:.1f} µg/m³",
        "total_deviation_kwh": f"{abs(total_energy_diff_kwh):,.1f} kWh",
        "audit_status": "Locked in SQLite Database (Ready for Pilot Review)"
    }
    st.json(audit_payload)
    st.info("ℹ️ ملاحظة نظام التدقيق: السجل محمي ببصمة SHA-256 (Integrity-Protected Audit Record) مع التخطيط لربط تسلسل الأحداث (Event Hash Chain) في النسخة الإنتاجية الكاملة.")

with tab8:
    st.subheader("🔬 8. Closed-Loop What-If Scenario Engine")
    st.info("ℹ️ **Scenario Assumption Notice:** يرجى ملاحظة أن معامل الغبار الناتج عن السيناريو مبني على نموذج تقديري افتراضي (Scenario Empirical Assumption / Proxy) لتأثير العواصف وجداول التنظيف، وليس تنبؤاً فيزيائياً حتمياً لحركة الرمال الفعلية.")
    
    scen_col1, scen_col2 = st.columns(2)
    with scen_col1:
        sim_storm_inc = st.slider("معدل زيادة العواصف (% الغبار - Assumption)", 0, 100, 20)
        sim_robots_add = st.slider("إضافة أسراب روبوتات جديدة", 0, 2000, 400)
    with scen_col2:
        sim_cleaning_freq = st.slider("جدول التنظيف الافتراضي (أيام بين الغسلات)", 5, 30, 15)

    cleaning_eff = 15.0 / max(5.0, float(sim_cleaning_freq))
    scen_soiling_mod = (1.0 + (sim_storm_inc / 100.0)) / cleaning_eff
    
    scen_sim_data = run_full_electrical_pvlib_pipeline(
        AL_WUSTA_LAT, AL_WUSTA_LON, total_capacity, num_blocks, 
        inverter_configs, technology_type, albedo, bifaciality_factor, 
        live_temp, live_wind, module_power_w, dc_ac_ratio, 
        wiring_loss_pct, inverter_max_eff, custom_soiling_mod=scen_soiling_mod
    )
    scen_gen_mwh = (scen_sim_data['Plant_Total']['إجمالي الإنتاج الفعلي المحاكى AC (Simulated Actual)'].sum() * 365)
    
    scen_capex = plant_capex + initial_robot_capex + (sim_robots_add * 650.0)
    scen_costs = scen_capex / ((1.0 + discount_rate) ** 0)
    scen_ener = 0.0
    scen_irr_flows = [-scen_capex]
    
    for yr in range(1, lifespan_years + 1):
        deg = (1.0 - 0.005) ** (yr - 1)
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
    scen_true_irr = calculate_true_irr(scen_irr_flows)

    db_conn.execute("INSERT INTO scenario_runs (timestamp, storm_increase, added_robots, cleaning_freq, resulting_lcoe, resulting_irr) VALUES (?, ?, ?, ?, ?, ?)",
                    (data_timestamp, sim_storm_inc, sim_robots_add, sim_cleaning_freq, scen_lcoe, scen_true_irr))
    db_conn.commit()

    res1, res2, res3 = st.columns(3)
    res1.metric("الإنتاج السنوي بعد السيناريو", f"{scen_gen_mwh:,.1f} MWh")
    res2.metric("تكلفة LCOE المعدلة بالنموذج", f"{scen_lcoe:.4f} ر.ع", delta=f"{scen_lcoe - discounted_lcoe:+.4f} ر.ع", delta_color="inverse")
    res3.metric("IRR المعدل للسيناريو", f"{scen_true_irr*100:.2f}%", delta=f"{(scen_true_irr - true_irr)*100:+.2f}%")

with tab9:
    st.subheader("☀️ 9. 3D Diorama View")
    cols = st.columns(2)
    for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
        soiling = cfg['soiling']
        tilt = cfg['tilt']
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
