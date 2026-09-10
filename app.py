import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import sqlite3
import hashlib
from datetime import datetime
from google import genai

st.set_page_config(page_title="RE-OPT Enterprise: Open-Meteo & Live SCADA", layout="wide")

st.title("⚡ RE-OPT Enterprise: Generalized Digital Twin & SCADA Operating System")
st.markdown("منصة التوأم الرقمي المؤسسي — مدعومة بالبيانات الحية عبر Open-Meteo ومحاكاة الـ SCADA لحظياً.")

# Database Initialization
@st.cache_resource
def init_db():
    conn = sqlite3.connect("re_opt_enterprise.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_orders (
            work_order_id TEXT PRIMARY KEY,
            timestamp TEXT,
            site_name TEXT,
            target_block TEXT,
            soil_percentage REAL,
            decision_type TEXT,
            confidence REAL,
            net_benefit REAL,
            status TEXT
        )
    """)
    cursor.execute("""
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

# **Open-Meteo Dynamic Weather Fetcher**
@st.cache_data(ttl=300)
def fetch_open_meteo_weather(latitude, longitude):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current=temperature_2m,wind_speed_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data.get("current", {})
            return current.get("temperature_2m", 35.0), current.get("wind_speed_10m", 6.0)
    except Exception:
        pass
    return 35.0, 6.0

# **Sidebar: Enterprise & Location Setup**
st.sidebar.subheader("🌍 إعدادات الموقع الجغرافي وقناة الطقس")
site_name = st.sidebar.text_input("اسم المحطة أو المشروع", value="Marmoul Solar Plant (Oman)")
lat = st.sidebar.number_input("خط العرض (Latitude)", value=18.1500, format="%.4f")
lon = st.sidebar.number_input("خط الطول (Longitude)", value=55.1800, format="%.4f")
timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Fetch live weather based on user coordinates
api_temp, api_wind = fetch_open_meteo_weather(lat, lon)

gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة AC (MW)", min_value=10.0, max_value=1000.0, value=150.0, step=10.0)
num_blocks = st.sidebar.selectbox("عدد محولات الطاقة الرئيسية (Inverter Blocks)", [2, 4, 6, 8], index=1)

connection_badge = f"🟢 OPEN-METEO LIVE — Temp: {api_temp}°C | Wind: {api_wind}m/s"
st.sidebar.markdown(f"**حالة الطقس الميداني:** `{connection_badge}`")
tariff = st.sidebar.number_input("تعرفة الطاقة (ر.ع / kWh)", value=0.025, format="%.3f")

st.sidebar.subheader("إعدادات الاتساخ والبيئة")
dust_storm_trigger = st.sidebar.toggle("🚨 محاكاة عاصفة رملية مفاجئة (تفعيل Stow Mode)", value=False)

inverter_telemetry = {}
for i in range(num_blocks):
    inv_name = f"Inverter Block {i+1}"
    with st.sidebar.expander(f"إعدادات {inv_name}", expanded=(i==0)):
        soil_val = st.slider(f"نسبة الغبار (%) - {inv_name}", 0.0, 45.0, float(4.0 + (i*2.5) + (15.0 if dust_storm_trigger else 0.0)), 0.5, key=f"soil_{i}")
        inv_temp_val = st.slider(f"حرارة العاكس الداخلية (°C) - {inv_name}", 30.0, 85.0, 56.0, 1.0, key=f"temp_{i}")
        inverter_telemetry[inv_name] = {'soiling': soil_val, 'inverter_temp': inv_temp_val}

# **Structured Decision Object**
def get_decision_object(soil_pct, block_cap_mw, tariff_val, wind_speed, inv_temp):
    recoverable_kwh = (block_cap_mw * 1000.0) * (soil_pct / 100.0) * 5.5
    revenue_at_risk = recoverable_kwh * tariff_val
    cleaning_cost = 45.0
    net_benefit = revenue_at_risk - cleaning_cost

    if wind_speed > 16.0 or inv_temp > 78.0:
        decision, confidence = "DELAY (Safety Lock)", 95.0
    elif net_benefit > 40.0 and soil_pct > 10.0:
        decision, confidence = "CLEAN", 94.0
    elif net_benefit > 0.0 and soil_pct > 6.0:
        decision, confidence = "DELAY", 78.0
    else:
        decision, confidence = "DO_NOT_CLEAN", 99.0

    return {
        "decision": decision,
        "confidence": confidence,
        "soiling_percentage": soil_pct,
        "energy_loss_mwh": recoverable_kwh / 1000.0,
        "revenue_at_risk_omr": revenue_at_risk,
        "net_benefit_omr": net_benefit
    }

# **Audit Chain Logger**
def log_audit_event(conn, event_id, site, operator, model_ver, input_hash, dec_obj, approval, wo_id):
    cursor = conn.cursor()
    cursor.execute("SELECT current_event_hash FROM audit_chain ORDER BY ROWID DESC LIMIT 1")
    last_row = cursor.fetchone()
    prev_hash = last_row[0] if last_row else "0" * 64
    
    reason_str = f"Site: {site}, Soil: {dec_obj['soiling_percentage']}%, NetBen: {dec_obj['net_benefit_omr']:.1f} OMR"
    raw_str = f"{prev_hash}-{event_id}-{timestamp_str}-{dec_obj['decision']}-{reason_str}-{input_hash}"
    curr_hash = hashlib.sha256(raw_str.encode()).hexdigest()
    
    cursor.execute("""
        INSERT OR REPLACE INTO audit_chain VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id, timestamp_str, site, operator, model_ver, input_hash, 
        dec_obj['decision'], reason_str, dec_obj['net_benefit_omr'], 
        approval, wo_id, prev_hash, curr_hash
    ))
    conn.commit()
    return curr_hash

# **Simulation Engine**
@st.cache_data
def run_simulation(latitude, longitude, total_cap_mw, n_inv, telemetry, t_amb, wind):
    tz = 'Asia/Muscat'
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    location = pvlib.location.Location(latitude, longitude, tz=tz)
    solpos = location.get_solarposition(times)
    clearsky = location.get_clearsky(times)
    
    block_ac_w = (total_cap_mw / n_inv) * 1e6
    block_dc_w = block_ac_w * 1.25
    inv_params = {'Paco': block_ac_w, 'Pdco': block_dc_w, 'Vdco': 600.0, 'Pso': block_ac_w * 0.01, 'C0':0, 'C1':0, 'C2':0, 'C3':0, 'Pnt':0}
    
    sim_results = {}
    total_ideal, total_actual = np.zeros(len(times)), np.zeros(len(times))
    
    for i, (inv_name, data) in enumerate(telemetry.items()):
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=22.0, surface_azimuth=180,
            solar_zenith=solpos['apparent_zenith'], solar_azimuth=solpos['azimuth'],
            dni=clearsky['dni'], ghi=clearsky['ghi'], dhi=clearsky['dhi'], albedo=0.4
        )
        poa_g = poa['poa_global'].fillna(0).clip(lower=0)
        cell_t = t_amb * np.ones(len(times))
        
        dc_ideal = pvlib.pvsystem.pvwatts_dc(g_poa_effective=poa_g, temp_cell=cell_t, pdc0=block_dc_w, gamma_pdc=-0.004).fillna(0).clip(lower=0)
        soil_loss = data['soiling'] / 100.0
        dc_actual = dc_ideal * (1.0 - soil_loss)
        
        v_nom = 600.0 * np.ones(len(times))
        ac_i = pvlib.inverter.sandia(v_nom, dc_ideal, inv_params).fillna(0) / 1000.0
        ac_a = pvlib.inverter.sandia(v_nom, dc_actual, inv_params).fillna(0) / 1000.0
        
        sim_results[inv_name] = pd.DataFrame({'المثالي (kW)': ac_i, 'الفعلي عبر SCADA (kW)': ac_a}, index=times)
        total_ideal += ac_i
        total_actual += ac_a

    sim_results['Plant_Total'] = pd.DataFrame({'إجمالي المثالي للمحطة (kW)': total_ideal, 'إجمالي الفعلي عبر SCADA (kW)': total_actual}, index=times)
    return sim_results

sim_data = run_simulation(lat, lon, total_capacity_mw, num_blocks, inverter_telemetry, api_temp, api_wind)
df_total = sim_data['Plant_Total']
total_ideal_sum = df_total['إجمالي المثالي للمحطة (kW)'].sum()
total_actual_sum = df_total['إجمالي الفعلي عبر SCADA (kW)'].sum()
current_pr_pct = (total_actual_sum / total_ideal_sum * 100.0) if total_ideal_sum > 0 else 0.0

# **Live Auto-Refreshing SCADA Fragment**
@st.fragment(run_every=4)
def live_scada_fragment():
    st.subheader("🟢 غرفة عمليات SCADA الحية (تحديث تلقائي كل 4 ثوانٍ)")
    jitter_power = (total_actual_sum / 1000.0) + np.random.normal(0, 0.5)
    jitter_freq = 50.0 + np.random.normal(0, 0.015)
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي القدرة المصدرة", f"{jitter_power:,.1f} MW", delta=f"{np.random.normal(0, 0.3):+.1f} MW")
    c2.metric("تردد الشبكة (Grid Hz)", f"{jitter_freq:.3f} Hz", delta="مستقر")
    c3.metric("درجة الحرارة (Open-Meteo)", f"{api_temp} °C")
    c4.metric("سرعة الرياح", f"{api_wind} m/s")

# **Dashboard Tabs**
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 غرفة العمليات والـ SCADA الحية", 
    "📈 منحنيات الأداء ومؤشر PR", 
    "☀️ كائنات القرار والاتساخ", 
    "📋 أوامر الشغل واعتماد الذكاء الاصطناعي",
    "🛡️ سجل التدقيق المشفر"
])

with tab1:
    live_scada_fragment()
    st.markdown("---")
    st.markdown("#### 📡 جدول قياسات الحساسات والعواكس الميدانية:")
    table_data = []
    for inv_name, data in inverter_telemetry.items():
        table_data.append({
            'المحول': inv_name,
            'نسبة الغبار (%)': f"{data['soiling']}%",
            'حرارة العاكس (°C)': f"{data['inverter_temp']} °C",
            'حالة التشغيل': "آمن وطبيعي" if data['inverter_temp'] < 75 else "⚠️ تنبيه حراري"
        })
    st.table(pd.DataFrame(table_data))

with tab2:
    st.subheader(f"📈 الإنتاجية الفعلية والنظرية لمشروع: {site_name}")
    st.metric("مؤشر الأداء العام (PR %)", f"{current_pr_pct:.1f}%")
    st.line_chart(df_total)

with tab3:
    st.subheader("☀️ كائنات القرار الهيكلي (Structured Decision Objects)")
    cols = st.columns(2)
    block_cap = total_capacity_mw / num_blocks
    for i, (inv_name, data) in enumerate(inverter_telemetry.items()):
        dec = get_decision_object(data['soiling'], block_cap, tariff, api_wind, data['inverter_temp'])
        is_cln = dec['decision'] == "CLEAN"
        border_c = "#ef4444" if is_cln else "#3b82f6"
        
        card = f"""
        <div style="background: #0f172a; border: 2px solid {border_c}; border-radius: 12px; padding: 16px; margin-bottom: 15px; color: white; text-align: right; direction: rtl;">
            <h3 style="margin:0; color: #f8fafc;">📌 {inv_name}</h3>
            <p style="margin: 8px 0;">القرار الاستراتيجي: <b>{dec['decision']}</b> (ثقة: {dec['confidence']}%)</p>
            <p style="margin: 4px 0; font-size: 13px;">الإيراد المهدد: <b>{dec['revenue_at_risk_omr']:,.1f} ر.ع</b> | صافي المنفعة: <b>{dec['net_benefit_omr']:,.1f} ر.ع</b></p>
        </div>
        """
        with cols[i % 2]:
            st.markdown(card, unsafe_allow_html=True)

with tab4:
    st.subheader("📋 بوابة اعتماد أوامر الشغل والذكاء الاصطناعي")
    selected_inv = st.selectbox("اختر المحول", [f"Inverter Block {i+1}" for i in range(num_blocks)])
    wo_code = f"WO-{np.random.randint(1000, 9999)}"
    ev_code = f"EVT-{np.random.randint(100000, 999999)}"
    
    soil_v = inverter_telemetry[selected_inv]['soiling']
    block_c = total_capacity_mw / num_blocks
    dec_payload = get_decision_object(soil_v, block_c, tariff, api_wind, inverter_telemetry[selected_inv]['inverter_temp'])
    st.json(dec_payload)

    if gemini_api_key and st.button("طلب تفسير استراتيجي من Gemini Copilot"):
        try:
            client = genai.Client(api_key=gemini_api_key)
            prompt = f"بصفتك مهندس طاقة، حلل كائن القرار وقدم توصية مهنية: {dec_payload}"
            res = client.models.generate_content(model='gemini-3.6-flash', contents=prompt)
            st.markdown(res.text)
        except Exception as e:
            st.error(f"خطأ: {e}")

    operator_name = st.text_input("اسم المشرف المسؤول", value="Mohammed Al Qutaiti (Lead Asset Operator)")
    approved = st.checkbox(f"أوافق على اعتماد قرار `{dec_payload['decision']}` لـ {selected_inv}")
    
    if approved and st.button("🚀 اعتماد وتوثيق الحدث في السلسلة المشفرة"):
        try:
            db_conn.execute(
                "INSERT OR REPLACE INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (wo_code, timestamp_str, site_name, selected_inv, soil_v, dec_payload['decision'], dec_payload['confidence'], dec_payload['net_benefit_omr'], "Approved & Dispatched")
            )
            inp_hash = hashlib.sha256(str(dec_payload).encode()).hexdigest()
            final_hash = log_audit_event(db_conn, ev_code, site_name, operator_name, "V2.1-OpenMeteo", inp_hash, dec_payload, "Approved", wo_code)
            st.success(f"✅ تم توثيق أمر الشغل بسجل التدقيق برقم بصمة: `{final_hash[:16]}...`")
        except Exception as e:
            st.error(f"خطأ: {e}")

    df_w = pd.read_sql("SELECT * FROM work_orders", db_conn)
    if not df_w.empty:
        st.dataframe(df_w, use_container_width=True)

with tab5:
    st.subheader("🛡️ سجل التدقيق المشفر (Tamper-Evident Audit Chain)")
    df_audit = pd.read_sql("SELECT * FROM audit_chain", db_conn)
    if not df_audit.empty:
        st.dataframe(df_audit, use_container_width=True)
    else:
        st.info("لا توجد أحداث مسجلة في السلسلة بعد.")
