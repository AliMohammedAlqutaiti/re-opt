import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import sqlite3
import hashlib
from datetime import datetime
from google import genai

st.set_page_config(page_title="RE-OPT Enterprise: Realistic Closed-Loop SCADA", layout="wide")

st.title("⚡ RE-OPT Enterprise: Real-Time Closed-Loop SCADA & Realistic Soiling Twin")
st.markdown("التوأم الرقمي المؤسسي — تراكم تدريجي وواقعي للغبار الصحراوي وفق المعايير الميدانية في عُمان.")

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
            return current.get("temperature_2m", 37.0), current.get("wind_speed_10m", 6.0)
    except Exception:
        pass
    return 37.0, 6.0

# **Sidebar Setup**
st.sidebar.subheader("🌍 إعدادات الموقع والتحكم الواقعي")
site_name = st.sidebar.text_input("اسم المحطة", value="Marmoul Solar Farm (Oman)")
lat = st.sidebar.number_input("خط العرض", value=18.1500, format="%.4f")
lon = st.sidebar.number_input("خط الطول", value=55.1800, format="%.4f")
timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

api_temp, api_wind = fetch_open_meteo_weather(lat, lon)
gemini_api_key = st.sidebar.text_input("مفتاح Gemini API Key", type="password")
total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة AC (MW)", 50.0, 500.0, 150.0, 50.0)
num_blocks = st.sidebar.selectbox("عدد محولات الطاقة (Inverter Blocks)", [2, 4, 6], index=1)
tariff = st.sidebar.number_input("تعرفة الكهرباء (ر.ع / kWh)", value=0.025, format="%.3f")

# **Initialize Session State with Realistic Initial Soiling**
if 'soiling_states' not in st.session_state:
    st.session_state.soiling_states = {
        f"Inverter Block {i+1}": float(2.0 + (i * 1.5)) for i in range(num_blocks)
    }

st.sidebar.subheader("⚙️ معدل تراكم الغبار الصحراوي (الواقعي)")
# تم تعديل المعدل ليصبح تدريجياً وبطيئاً جداً يطابق الواقع الميداني
dust_accumulation_speed = st.sidebar.slider(
    "سرعة تراكم الغبار لكل دورة تحديث (%)", 
    min_value=0.001, max_value=0.05, value=0.008, step=0.001, format="%.3f"
)

# **Structured Decision Object**
def get_decision_object(soil_pct, block_cap_mw, tariff_val, wind_speed):
    recoverable_kwh = (block_cap_mw * 1000.0) * (soil_pct / 100.0) * 5.5
    revenue_at_risk = recoverable_kwh * tariff_val
    cleaning_cost = 45.0
    net_benefit = revenue_at_risk - cleaning_cost

    if wind_speed > 16.0:
        decision, confidence = "DELAY (Safety Lock)", 95.0
    elif net_benefit > 35.0 and soil_pct > 8.0:
        decision, confidence = "CLEAN", 94.0
    elif net_benefit > 0.0 and soil_pct > 4.0:
        decision, confidence = "DELAY", 78.0
    else:
        decision, confidence = "DO_NOT_CLEAN", 99.0

    return {
        "decision": decision,
        "confidence": confidence,
        "soiling_percentage": soil_pct,
        "revenue_at_risk_omr": revenue_at_risk,
        "net_benefit_omr": net_benefit
    }

# **Audit Chain Logger**
def log_audit_event(conn, event_id, site, operator, model_ver, input_hash, dec_obj, approval, wo_id):
    cursor = conn.cursor()
    cursor.execute("SELECT current_event_hash FROM audit_chain ORDER BY ROWID DESC LIMIT 1")
    last_row = cursor.fetchone()
    prev_hash = last_row[0] if last_row else "0" * 64
    
    reason_str = f"Site: {site}, Soil: {dec_obj['soiling_percentage']:.2f}%, NetBen: {dec_obj['net_benefit_omr']:.1f} OMR"
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

# **Auto-Refreshing SCADA Fragment with Realistic Closed-Loop Soiling**
@st.fragment(run_every=3)
def live_scada_control_room():
    st.subheader("🟢 غرفة عمليات SCADA الحية (تراكم واقعي تدريجي)")
    
    # Accumulate dust micro-incrementally per tick
    for block in st.session_state.soiling_states:
        if st.session_state.soiling_states[block] < 35.0:
            st.session_state.soiling_states[block] += dust_accumulation_speed
            st.session_state.soiling_states[block] = min(35.0, st.session_state.soiling_states[block])

    block_cap = total_capacity_mw / num_blocks
    table_data = []
    
    cols = st.columns(num_blocks)
    for i, (inv_name, soil_val) in enumerate(st.session_state.soiling_states.items()):
        dec = get_decision_object(soil_val, block_cap, tariff, api_wind)
        
        table_data.append({
            'محول الطاقة': inv_name,
            'نسبة الغبار الفعلي (%)': f"{soil_val:.2f}%",
            'القرار الآلي': dec['decision'],
            'الإيراد المهدد': f"{dec['revenue_at_risk_omr']:,.1f} ر.ع"
        })
        
        with cols[i]:
            color = "#ef4444" if dec['decision'] == "CLEAN" else "#3b82f6"
            st.markdown(f"""
            <div style="background: #0f172a; border: 2px solid {color}; border-radius: 10px; padding: 12px; color: white; text-align: right; direction: rtl;">
                <h4>{inv_name}</h4>
                <p>الغبار: <b>{soil_val:.2f}%</b></p>
                <p>الحالة: <b>{dec['decision']}</b></p>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 📡 تدفق بيانات أسراب الروبوتات والعواكس لحظياً:")
    st.table(pd.DataFrame(table_data))

# **Dashboard Tabs**
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 غرفة العمليات الحية (SCADA)", 
    "☀️ كائنات القرار والتحكم الآلي", 
    "📋 أوامر الشغل والتنظيف الذاتي",
    "🛡️ سجل التدقيق المشفر"
])

with tab1:
    live_scada_control_room()
    st.caption("ملاحظة: تزداد نسبة الغبار الآن بمعدل دقيق وميكروسكوبي واقعي يحاكي الظروف الصحراوية ببطء واستقرار.")

with tab2:
    st.subheader("☀️ المراقبة التفصيلية لكائنات القرار الهيكلي")
    block_cap = total_capacity_mw / num_blocks
    cols = st.columns(2)
    for i, (inv_name, soil_val) in enumerate(st.session_state.soiling_states.items()):
        dec = get_decision_object(soil_val, block_cap, tariff, api_wind)
        with cols[i % 2]:
            st.info(f"**{inv_name}** | نسبة الغبار: {soil_val:.2f}% | القرار: **{dec['decision']}** (ثقة: {dec['confidence']}%)")

with tab3:
    st.subheader("📋 تنفيذ الصيانة وإعادة ضبط الغبار تلقائياً (Reset to Clean)")
    selected_inv = st.selectbox("اختر المحول لتنفيذ دورة التنظيف", list(st.session_state.soiling_states.keys()))
    
    soil_val = st.session_state.soiling_states[selected_inv]
    block_cap = total_capacity_mw / num_blocks
    dec_payload = get_decision_object(soil_val, block_cap, tariff, api_wind)
    
    st.json(dec_payload)
    
    operator_name = st.text_input("اسم المشرف المسؤول", value="Mohammed Al Qutaiti (Lead Asset Operator)")
    approve_cleaning = st.checkbox(f"تأكيد إطلاق روبوتات التنظيف لـ {selected_inv}")
    
    if approve_cleaning and st.button("🚀 إرسال أمر الشغل وتنظيف الألواح فوراً"):
        # **The Closed-Loop Realistic Reset Mechanism**
        st.session_state.soiling_states[selected_inv] = 0.5  # Reset back to pristine clean state (0.5%)
        
        wo_code = f"WO-CLEAN-{np.random.randint(1000, 9999)}"
        ev_code = f"EVT-{np.random.randint(100000, 999999)}"
        
        try:
            db_conn.execute(
                "INSERT OR REPLACE INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (wo_code, timestamp_str, site_name, selected_inv, soil_val, "CLEAN", dec_payload['confidence'], dec_payload['net_benefit_omr'], "Executed & Reset")
            )
            inp_hash = hashlib.sha256(str(dec_payload).encode()).hexdigest()
            final_hash = log_audit_event(db_conn, ev_code, site_name, operator_name, "RealisticClosedLoop-V2.1", inp_hash, dec_payload, "Executed Cleaning", wo_code)
            st.success(f"✅ تم تنظيف المحول `{selected_inv}` وإعادة ضبط نسبة الغبار إلى 0.5% بنجاح! بصمة الحدث: `{final_hash[:16]}...`")
            st.rerun()
        except Exception as e:
            st.error(f"خطأ: {e}")

    df_w = pd.read_sql("SELECT * FROM work_orders", db_conn)
    if not df_w.empty:
        st.dataframe(df_w, use_container_width=True)

with tab4:
    st.subheader("🛡️ سجل التدقيق المشفر (Tamper-Evident Audit Chain)")
    df_audit = pd.read_sql("SELECT * FROM audit_chain", db_conn)
    if not df_audit.empty:
        st.dataframe(df_audit, use_container_width=True)
    else:
        st.info("لا توجد أحداث مسجلة في السلسلة بعد.")
