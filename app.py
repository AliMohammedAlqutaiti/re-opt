import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import sqlite3
import hashlib
from datetime import datetime
from google import genai

st.set_page_config(page_title="RE-OPT: Marmoul 3D Solar Twin", layout="wide")

st.title("⚡ RE-OPT: Marmoul Solar Plant - Cryptographic Audit Chain Edition")
st.markdown("التوأم الرقمي المؤسسي — النسخة المحدثة مع سجل التدقيق المشفر المؤمن (Tamper-Evident Event Chain).")

# تهيئة قاعدة البيانات المحلية SQLite (الجداول المحدثة للخطوتين 1 و 3)
@st.cache_resource
def init_db():
    conn = sqlite3.connect("re_opt_marmoul.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_orders (
            work_order_id TEXT PRIMARY KEY,
            timestamp TEXT,
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

@st.cache_data(ttl=600)
def fetch_live_weather(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data.get("current", {})
            return current.get("temperature_2m", 37.0), current.get("wind_speed_10m", 8.0)
    except Exception:
        pass
    return 37.0, 8.0

lat, lon = 18.15, 55.18
site_name = "Marmoul Solar Farm, Al Wusta (Oman)"
timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

api_temp, api_wind = fetch_live_weather(lat, lon)

gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=1000.0, value=150.0, step=50.0)
total_capacity = total_capacity_mw * 1000 
num_blocks = st.sidebar.selectbox("عدد محولات الطاقة الرئيسية (Inverter Blocks)", [2, 4, 6, 8], index=1)

scada_mode = st.sidebar.toggle("تفعيل الربط الحي مع أنظمة SCADA", value=True)

if scada_mode:
    live_temp = api_temp
    live_wind = api_wind
else:
    live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=60.0, value=float(api_temp), step=0.5)
    live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind*3.6), step=0.5)
    live_wind = live_wind_kmh / 3.6

st.sidebar.subheader("محاكاة العواصف الرملية في صحراء الوسطى")
dust_storm_active = st.sidebar.toggle("🚨 محاكاة عاصفة رملية مفاجئة", value=False)
storm_soiling_penalty = st.sidebar.slider("معامل الغبار الإضافي (%)", min_value=5.0, max_value=50.0, value=18.0, step=2.5) if dust_storm_active else 0.0

st.sidebar.subheader("تخصيص الألواح وزوايا الميل")
technology_type = st.sidebar.selectbox("نوع تكنولوجيا الألواح", ["ثنائية الوجه (Bifacial Glass-Glass)", "أحادية الوجه (Mono-facial PERC)"])
albedo = st.sidebar.slider("معامل انعكاس رمال الوسطى (Albedo)", min_value=0.2, max_value=0.7, value=0.45, step=0.05)
bifaciality_factor = st.sidebar.slider("معامل ثنائية الوجه (%)", min_value=65.0, max_value=85.0, value=75.0, step=5.0) / 100.0
tariff = st.sidebar.number_input("تعرفة الطاقة (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.025, step=0.001, format="%.3f")

st.sidebar.subheader("اقتصاديات أسطول الروبوتات الجافة")
total_robots = st.sidebar.number_input("عدد روبوتات التنظيف الجاف", min_value=500, max_value=6000, value=2200, step=100)
initial_robot_capex = st.sidebar.number_input("الاستثمار الأولي للروبوتات (ر.ع)", min_value=500000.0, max_value=6000000.0, value=1500000.0, step=50000.0)
daily_robot_depreciation = st.sidebar.number_input("إهلاك الصيانة اليومي", min_value=10.0, max_value=600.0, value=55.0, step=5.0)

st.sidebar.subheader("التحكم المستقل لمحولات الطاقة (Inverter Blocks)")
inverter_configs = {}
for i in range(num_blocks):
    inv_name = f"Inverter Block {i+1}"
    with st.sidebar.expander(f"إعدادات {inv_name}", expanded=(i==0)):
        base_s = 3.0 + (i * 2.0)
        inv_soiling = st.slider(f"نسبة الغبار والترسبات (%) - {inv_name}", min_value=0.0, max_value=45.0, value=float(base_s + storm_soiling_penalty), step=0.5, key=f"soil_{i}")
        inv_tilt = st.slider(f"زاوية ميل الألواح (Tilt °) - {inv_name}", min_value=5.0, max_value=45.0, value=float(22.0), step=1.0, key=f"tilt_{i}")
        inverter_configs[inv_name] = {'soiling': inv_soiling, 'tilt': inv_tilt}

# دالة كائن القرار الهيكلي (Structured Decision Object)
def get_cleaning_decision_object(block_name, soil_pct, block_cap_mw, tariff_val, wind_speed, is_storm):
    recoverable_kwh = (block_cap_mw * 1000.0) * (soil_pct / 100.0) * 5.5
    revenue_at_risk = recoverable_kwh * tariff_val
    cleaning_cost = 45.0 
    
    weather_risk = "HIGH" if wind_speed > 15.0 or is_storm else ("MODERATE" if wind_speed > 10.0 else "LOW")
    net_benefit = revenue_at_risk - cleaning_cost

    if is_storm or weather_risk == "HIGH":
        decision = "DELAY"
        confidence = 88.0
    elif net_benefit > 50.0 and soil_pct > 12.0:
        decision = "CLEAN"
        confidence = 94.5
    elif net_benefit > 0.0 and soil_pct > 8.0:
        decision = "DELAY"
        confidence = 76.0
    else:
        decision = "DO_NOT_CLEAN"
        confidence = 99.0

    return {
        "decision": decision,
        "confidence": confidence,
        "soiling_percentage": soil_pct,
        "energy_loss_mwh": recoverable_kwh / 1000.0,
        "revenue_at_risk_omr": revenue_at_risk,
        "cleaning_cost_omr": cleaning_cost,
        "expected_net_benefit_omr": net_benefit,
        "weather_risk": weather_risk
    }

# دالة تسجيل الأحداث في سلسلة التدقيق المشفرة (Tamper-Evident Audit Chain)
def log_audit_event_to_chain(conn, event_id, operator, model_version, input_hash, decision_obj, approval, wo_id):
    cursor = conn.cursor()
    cursor.execute("SELECT current_event_hash FROM audit_chain ORDER BY ROWID DESC LIMIT 1")
    last_row = cursor.fetchone()
    prev_hash = last_row[0] if last_row else "0" * 64
    
    reason_str = f"Soiling: {decision_obj['soiling_percentage']}%, NetBenefit: {decision_obj['expected_net_benefit_omr']:.1f} OMR, Weather: {decision_obj['weather_risk']}"
    raw_chain_str = f"{prev_hash}-{event_id}-{timestamp_str}-{decision_obj['decision']}-{reason_str}-{input_hash}"
    curr_hash = hashlib.sha256(raw_chain_str.encode()).hexdigest()
    
    cursor.execute("""
        INSERT OR REPLACE INTO audit_chain VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id, timestamp_str, operator, model_version, input_hash, 
        decision_obj['decision'], reason_str, decision_obj['expected_net_benefit_omr'], 
        approval, wo_id, prev_hash, curr_hash
    ))
    conn.commit()
    return curr_hash

@st.cache_data
def run_marmoul_simulation(latitude, longitude, total_cap, n_inv, configs, tech_mode, alb, bif_factor, t_amb, wind, is_live):
    tz = 'Asia/Muscat'
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    location = pvlib.location.Location(latitude, longitude, tz=tz)
    clearsky = location.get_clearsky(times)
    
    peak_ghi = 1000.0
    ghi = clearsky['ghi']
    
    noct = 47.0
    cell_temp = t_amb + (ghi / 800.0) * (noct - 20.0) * (9.5 / (5.7 + 3.8 * wind))
    temp_coeff = -0.0034
    temp_factor = 1.0 + temp_coeff * (cell_temp - 25.0)
    temp_factor = temp_factor.clip(lower=0.5)
    
    block_capacity = total_cap / n_inv
    simulation_results = {}
    total_actual = np.zeros(len(times))
    total_ideal = np.zeros(len(times))
    
    for i in range(n_inv):
        inv_name = f"Inverter Block {i+1}"
        cfg = configs[inv_name]
        
        base_power = (ghi / peak_ghi) * block_capacity
        base_power = base_power.clip(lower=0)
        
        ideal_power = base_power * temp_factor
        if "ثنائية الوجه" in tech_mode:
            bif_gain = 1.0 + (alb * bif_factor * 0.20)
            ideal_power *= bif_gain
            
        total_degradation = cfg['soiling'] + (abs(cfg['tilt'] - 22.0) * 0.2)
        actual_factor = max(0.0, 1.0 - (total_degradation / 100.0))
        
        if is_live:
            np.random.seed(300 + i)
            noise = np.random.normal(1.0, 0.006, len(times))
            actual_power = ideal_power * actual_factor * noise
        else:
            actual_power = ideal_power * actual_factor
            
        simulation_results[inv_name] = pd.DataFrame({
            'النموذج المثالي': ideal_power,
            'قراءات السكادا الفعلية': actual_power
        }, index=times)
        
        total_ideal += ideal_power
        total_actual += actual_power

    simulation_results['Plant_Total'] = pd.DataFrame({
        'إجمالي المثالي للمحطة': total_ideal,
        'إجمالي قراءات السكادا الفعلية': total_actual
    }, index=times)
    
    return simulation_results

sim_data = run_marmoul_simulation(lat, lon, total_capacity, num_blocks, inverter_configs, technology_type, albedo, bifaciality_factor, live_temp, live_wind, scada_mode)

df_total = sim_data['Plant_Total']
total_plant_loss_kwh = (df_total['إجمالي المثالي للمحطة'] - df_total['إجمالي قراءات السكادا الفعلية']).sum()
daily_financial_loss = max(0.0, abs(total_plant_loss_kwh) * tariff)
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

annual_generation_mwh = (df_total['إجمالي قراءات السكادا الفعلية'].sum() * 365) / 1000.0
plant_capex = total_capacity_mw * 330000.0 
total_lifetime_cost = plant_capex + initial_robot_capex + (daily_robot_depreciation * 365 * 25)
total_lifetime_generation_mwh = annual_generation_mwh * 25
lcoe = total_lifetime_cost / max(1.0, total_lifetime_generation_mwh * 1000)

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📈 التوأم الرقمي لمحطة مرمول", 
    "🔍 التشخيص الذكي (FDD)", 
    "☀️ العرض المرئي ثلاثي الأبعاد والقرارات", 
    "💰 الاقتصاديات و LCOE", 
    "📋 أوامر الشغل والاعتماد",
    "🛡️ سجل التدقيق المشفر (Audit Chain)"
])

with tab1:
    if dust_storm_active:
        st.error("🚨 **تحذير طارئ في مرمول:** عاصفة رملية تؤثر على حقول الطاقة بالوسطى وتم تفعيل طوارئ الروبوتات.")
    
    st.subheader(f"📈 إنتاجية المحطة الفعلية ({site_name})")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي القدرة", f"{total_capacity_mw} MW")
    c2.metric("الفارق الإنتاجي", f"{abs(total_plant_loss_kwh):,.1f} kWh")
    c3.metric("تعرفة الكهرباء", f"{tariff:.3f} / kWh")
    c4.metric("حالة الموقع", "عاصفة نشطة" if dust_storm_active else "مستقر آلياً")
    st.markdown("---")
    st.line_chart(df_total)

with tab2:
    st.subheader("🔍 خوارزميات الكشف عن الأعطال وتراكم الغبار (FDD)")
    fdd_summary = []
    for inv_name, df_block in sim_data.items():
        if inv_name == 'Plant_Total':
            continue
        ideal_sum = df_block['النموذج المثالي'].sum()
        actual_sum = df_block['قراءات السكادا الفعلية'].sum()
        deviation_pct = ((ideal_sum - actual_sum) / ideal_sum) * 100 if ideal_sum > 0 else 0
        
        if deviation_pct > 12.0 or dust_storm_active:
            status = "🚨 تنبيه حرج: ترسبات رمال عالية"
        elif deviation_pct > 5.0:
            status = "⚠️ تنبيه متوسط: انحراف في أداء المحول"
        else:
            status = "✅ أداء طبيعي ومستقر"
            
        fdd_summary.append({
            'المحول (Inverter Block)': inv_name,
            'نسبة الانحراف الفعلي (%)': f"{deviation_pct:.2f}%",
            'الحالة التشخيصية': status
        })
    st.table(pd.DataFrame(fdd_summary))

with tab3:
    st.subheader("☀️ العرض المرئي ثلاثي الأبعاد وكائن القرار الهيكلي (Decision Object)")
    cols = st.columns(2)
    block_cap_mw = total_capacity_mw / num_blocks
    
    for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
        soiling, tilt = cfg['soiling'], cfg['tilt']
        dec_obj = get_cleaning_decision_object(inv_name, soiling, block_cap_mw, tariff, live_wind, dust_storm_active)
        
        is_critical = dec_obj['decision'] == "CLEAN"
        border_color = "#ef4444" if is_critical else ("#f59e0b" if dec_obj['decision'] == "DELAY" else "#3b82f6")
        bg_cells = "#fee2e2" if is_critical else "#eff6ff"
        cell_grid_border = "#fca5a5" if is_critical else "#93c5fd"
        cell_bg = "#fef2f2" if is_critical else "#dbeafe"

        html_panel_card = f"""
        <div style="background: linear-gradient(135deg, #1e293b, #0f172a); border: 3px solid {border_color}; border-radius: 16px; padding: 20px; margin-bottom: 20px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4); color: white; font-family: sans-serif; text-align: right; direction: rtl;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="margin: 0; color: #f8fafc; font-size: 18px;">📌 {inv_name}</h3>
                <span style="background: {border_color}; color: white; padding: 4px 10px; border-radius: 8px; font-size: 12px; font-weight: bold;">القرار: {dec_obj['decision']} (ثقة: {dec_obj['confidence']}%)</span>
            </div>
            <div style="background: {bg_cells}; border: 2px solid {border_color}; border-radius: 8px; padding: 10px; display: grid; grid-template-columns: repeat(4, 1fr); grid-template-rows: repeat(3, 1fr); gap: 6px; height: 120px; margin-bottom: 15px;">
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 4px;"></div>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 13px; background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 6px; margin-bottom: 6px;">
                <span> الغبار: <b>{soiling}%</b></span>
                <span> الإيراد المهدد: <b>{dec_obj['revenue_at_risk_omr']:,.1f} ر.ع</b></span>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 13px; background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 6px;">
                <span> صافي المنفعة: <b>{dec_obj['expected_net_benefit_omr']:,.1f} ر.ع</b></span>
                <span> مخاطر الطقس: <b>{dec_obj['weather_risk']}</b></span>
            </div>
        </div>
        """
        with cols[i % 2]:
            st.markdown(html_panel_card, unsafe_allow_html=True)

with tab4:
    st.subheader("💰 التحليل المالي بعيد المدى واقتصاديات أسطول الروبوتات (LCOE & CAPEX)")
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)
    f_col1.metric("استثمار الروبوتات", f"{initial_robot_capex:,.0f} ر.ع")
    f_col2.metric("إهلاك الصيانة اليومي", f"{daily_robot_depreciation:.2f} ر.ع")
    f_col3.metric("صافي العائد اليومي", f"{net_robotic_roi:,.2f} ر.ع")
    f_col4.metric("تكلفة LCOE", f"{lcoe:.4f} ر.ع")

with tab5:
    st.subheader("📋 توليد أوامر الشغل وبوابة الاعتماد (Human-in-the-Loop)")
    selected_block_wo = st.selectbox("اختر المحول لإصدار أمر العمل", [f"Inverter Block {i+1}" for i in range(num_blocks)])
    work_order_id = f"WO-MARMOUL-2026-{np.random.randint(1000, 9999)}"
    event_id = f"EVT-{np.random.randint(100000, 999999)}"
    
    soil_val = inverter_configs[selected_block_wo]['soiling']
    block_cap_mw = total_capacity_mw / num_blocks
    current_dec_obj = get_cleaning_decision_object(selected_block_wo, soil_val, block_cap_mw, tariff, live_wind, dust_storm_active)
    
    operator_name = st.text_input("اسم المشرف المسؤول", value="Mohammed Al Qutaiti (Lead Asset Operator)")
    approval_toggle = st.checkbox(f"أوافق بصفتي مشرف العمليات على تنفيذ قرار `{current_dec_obj['decision']}` لـ {selected_block_wo}")
    
    st.json(current_dec_obj)
    
    if approval_toggle:
        if st.button("🚀 اعتماد وتصدير أمر الشغل وتوثيق الحدث في السلسلة المشفرة"):
            try:
                # 1. حفظ أمر الشغل
                db_conn.execute(
                    "INSERT INTO work_orders (work_order_id, timestamp, target_block, soil_percentage, decision_type, confidence, net_benefit, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (work_order_id, timestamp_str, selected_block_wo, soil_val, current_dec_obj['decision'], current_dec_obj['confidence'], current_dec_obj['expected_net_benefit_omr'], "Dispatched & Approved")
                )
                
                # 2. توليد وتسجيل الحدث في سلسلة التدقيق المشفرة (Audit Chain)
                input_hash = hashlib.sha256(str(current_dec_obj).encode()).hexdigest()
                final_hash = log_audit_event_to_chain(
                    db_conn, event_id, operator_name, "RE-OPT-V2.1-OQ", input_hash, 
                    current_dec_obj, "Approved by Human Operator", work_order_id
                )
                
                st.success(f"✅ تم إصدار أمر الشغل `{work_order_id}` وتوثيق الحدث المشفر بنجاح برقم بصمة: `{final_hash[:16]}...`")
            except Exception as e:
                st.error(f"خطأ أثناء الحفظ: {e}")

    st.markdown("---")
    st.markdown("### 🗄️ سجل أوامر الشغل المحفوظة:")
    df_wo = pd.read_sql("SELECT * FROM work_orders", db_conn)
    if not df_wo.empty:
        st.dataframe(df_wo, use_container_width=True)
    else:
        st.info("ℹ️ لا توجد أوامر شغل مسجلة حالياً.")

with tab6:
    st.subheader("🛡️ سجل التدقيق المشفر (Tamper-Evident Audit Chain Ledger)")
    st.markdown("سجل أحداث العمليات المؤسسي المؤمّن بربط السلاسل الت cryptographic (`Previous Event Hash` ⇄ `Current Event Hash`):")
    df_audit = pd.read_sql("SELECT * FROM audit_chain", db_conn)
    if not df_audit.empty:
        st.dataframe(df_audit, use_container_width=True)
    else:
        st.info("ℹ️ لا توجد أحداث مسجلة في سلسلة التدقيق حتى الآن. قم باعتماد أمر شغل من التبويب السابق لتوليد أول حدث مشفر.")
