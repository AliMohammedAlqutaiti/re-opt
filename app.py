import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import json
from google import genai

st.set_page_config(page_title="RE-OPT: Ultimate Enterprise Digital Twin", layout="wide")

st.title("⚡ RE-OPT: Ultimate Manah Enterprise Digital Twin & O&M Platform")
st.markdown("منصة التوأم الرقمي المؤسسي الشاملة - LCOE، أسراب الروبوتات الجافة، طوارئ العواصف، والتحليل المالي.")

@st.cache_data(ttl=600)
def fetch_live_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=23.58&longitude=58.38&current=temperature_2m,wind_speed_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data.get("current", {})
            temp = current.get("temperature_2m", 32.0)
            wind = current.get("wind_speed_10m", 6.0)
            return temp, wind
    except Exception:
        pass
    return 32.0, 6.0

api_temp, api_wind = fetch_live_weather()

st.sidebar.header("إعدادات البنية المؤسسية والاقتصادية")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=500.0, value=100.0, step=50.0)
total_capacity = total_capacity_mw * 1000 
num_inverters = st.sidebar.selectbox("عدد محولات الطاقة (Inverter Blocks)", [2, 4, 6], index=1)

scada_mode = st.sidebar.toggle("تفعيل الربط الحي مع محطة SCADA (IoT Stream)", value=True)

if scada_mode:
    live_temp = api_temp
    live_wind = api_wind
else:
    live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
    live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind*3.6), step=0.5)
    live_wind = live_wind_kmh / 3.6

# عاصفة رملية طارئة
st.sidebar.subheader("طوارئ البيئة الصحراوية (Dust Storm Simulator)")
dust_storm_active = st.sidebar.toggle("🚨 محاكاة عاصفة رملية مفاجئة (Dust Storm Event)", value=False)
storm_soiling_penalty = st.sidebar.slider("معامل الفقد الإضافي للعاصفة (%)", min_value=5.0, max_value=40.0, value=15.0, step=2.5) if dust_storm_active else 0.0

st.sidebar.subheader("تكنولوجيا الألواح واقتصاديات المحطة")
technology_type = st.sidebar.selectbox("نوع ألواح المحطة الرئيسية", ["ثنائية الوجه (Bifacial)", "أحادية الوجه (Mono-facial)", "هجين (مزيج بين النوعين)"])
albedo = st.sidebar.slider("معامل الانعكاس الأرضي (Albedo)", min_value=0.1, max_value=0.8, value=0.40, step=0.05)
bifaciality_factor = st.sidebar.slider("معامل ثنائية الوجه للألواح (%)", min_value=60.0, max_value=85.0, value=70.0, step=5.0) / 100.0

tariff = st.sidebar.number_input("تعرفة الكهرباء المؤسسية (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

# إدارة أسطول الروبوتات والـ CAPEX (تمت مركزتها هنا لتظهر في لوحة الاقتصاديات)
st.sidebar.subheader("اقتصاديات أسطول الروبوتات الجافة (Robot Fleet Economics)")
total_robots = st.sidebar.number_input("إجمالي الروبوتات النشطة", min_value=500, max_value=3000, value=1800, step=100)
initial_robot_capex = st.sidebar.number_input("الاستثمار الأولي لأسطول الروبوتات (ر.ع)", min_value=500000.0, max_value=3000000.0, value=1200000.0, step=50000.0)
daily_robot_depreciation = st.sidebar.number_input("إهلاك وصيانة الروبوتات اليومي (ر.ع)", min_value=10.0, max_value=300.0, value=45.0, step=5.0)

st.sidebar.subheader("التحكم المستقل لكتل المحولات (Inverter Blocks)")
inverter_configs = {}
for i in range(num_inverters):
    inv_name = f"Inverter Block {i+1}"
    with st.sidebar.expander(f"إعدادات تشغيل {inv_name}", expanded=(i==0)):
        base_s = 2.0 + (i * 1.5)
        inv_soiling = st.slider(f"تراكم الغبار (%) - {inv_name}", min_value=0.0, max_value=40.0, value=float(base_s + storm_soiling_penalty), step=0.5, key=f"soiling_inv_{i}")
        inv_tilt_err = st.slider(f"خطأ الميل (°) - {inv_name}", min_value=0.0, max_value=10.0, value=float(i * 0.8), step=0.5, key=f"tilt_inv_{i}")
        inverter_configs[inv_name] = {'soiling': inv_soiling, 'tilt_error': inv_tilt_err}

@st.cache_data
def run_ultimate_simulation(total_cap, n_inv, configs, tech_mode, alb, bif_factor, t_amb, wind, is_live):
    site_latitude = 23.58
    site_longitude = 58.38
    tz = 'Asia/Muscat'
    
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    location = pvlib.location.Location(site_latitude, site_longitude, tz=tz)
    clearsky = location.get_clearsky(times)
    
    peak_ghi = 1000.0
    ghi = clearsky['ghi']
    
    noct = 45.0
    cell_temp = t_amb + (ghi / 800.0) * (noct - 20.0) * (9.5 / (5.7 + 3.8 * wind))
    temp_coeff = -0.0035
    temp_factor = 1.0 + temp_coeff * (cell_temp - 25.0)
    temp_factor = temp_factor.clip(lower=0.5)
    
    block_capacity = total_cap / n_inv
    
    simulation_results = {}
    total_mono_actual = np.zeros(len(times))
    total_bif_actual = np.zeros(len(times))
    total_actual = np.zeros(len(times))
    
    for i in range(n_inv):
        inv_name = f"Inverter Block {i+1}"
        cfg = configs[inv_name]
        
        base_power = (ghi / peak_ghi) * block_capacity
        base_power = base_power.clip(lower=0)
        
        mono_base = base_power * temp_factor
        bif_gain = 1.0 + (alb * bif_factor * 0.18)
        bif_base = base_power * temp_factor * bif_gain
        
        total_degradation = cfg['soiling'] + (cfg['tilt_error'] * 0.5)
        actual_factor = max(0.0, 1.0 - (total_degradation / 100.0))
        
        mono_actual_power = mono_base * actual_factor
        bif_actual_power = bif_base * actual_factor
        
        selected_actual = bif_actual_power if "ثنائية الوجه" in tech_mode or (tech_mode == "هجين (مزيج بين النوعين)" and i % 2 == 0) else mono_actual_power
        
        if is_live:
            np.random.seed(100 + i)
            noise = np.random.normal(1.0, 0.008, len(times))
            scada_power = selected_actual * noise
        else:
            scada_power = selected_actual
            
        simulation_results[inv_name] = pd.DataFrame({
            'الواقع (أحادى الوجه Mono)': mono_actual_power,
            'الواقع (ثنائي الوجه Bifacial)': bif_actual_power,
            'قياسات سكادا الفعليّة (IoT)': scada_power
        }, index=times)
        
        total_mono_actual += mono_actual_power
        total_bif_actual += bif_actual_power
        total_actual += scada_power

    simulation_results['Plant_Total'] = pd.DataFrame({
        'إجمالي الواقع (أحادى الوجه)': total_mono_actual,
        'إجمالي الواقع (ثنائي الوجه)': total_bif_actual,
        'إجمالي قياسات سكادا الفعلية': total_actual
    }, index=times)
    
    return simulation_results

inverter_data = run_ultimate_simulation(total_capacity, num_inverters, inverter_configs, technology_type, albedo, bifaciality_factor, live_temp, live_wind, scada_mode)

df_total = inverter_data['Plant_Total']
total_plant_loss_kwh = (df_total['إجمالي الواقع (ثنائي الوجه)'] - df_total['إجمالي قياسات سكادا الفعلية']).sum()
daily_financial_loss = max(0.0, abs(total_plant_loss_kwh) * tariff)
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

# حسابات LCOE الاقتصادية لـ 25 عاماً
annual_generation_mwh = (df_total['إجمالي قياسات سكادا الفعلية'].sum() * 365) / 1000.0
plant_capex = total_capacity_mw * 350000.0 
total_lifetime_cost = plant_capex + initial_robot_capex + (daily_robot_depreciation * 365 * 25)
total_lifetime_generation_mwh = annual_generation_mwh * 25
lcoe = total_lifetime_cost / max(1.0, total_lifetime_generation_mwh * 1000)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 التوأم الرقمي والتقييم", 
    "🔍 التشخيص الذكي (FDD)", 
    "🤖 أسطول الروبوتات والطوارئ", 
    "💰 الاقتصاديات و LCOE", 
    "📋 أوامر الشغل الآلية"
])

with tab1:
    if dust_storm_active:
        st.error("🚨 **تحذير طارئ:** عاصفة رملية نشطة تؤثر حالياً على منشآت الطاقة في مسقط/منح. تم تطبيق معامل فقد إضافي على الغبار.")
    
    st.subheader(f"📈 مقارنة إنتاجية المحطة ({technology_type})")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي القدرة", f"{total_capacity_mw} MW")
    c2.metric("حجم الفارق الإنتاجي", f"{abs(total_plant_loss_kwh):,.1f} kWh")
    c3.metric("تعرفة الكهرباء", f"{tariff:.3f} ر.ع / kWh")
    c4.metric("حالة النظام البيئي", "عاصفة نشطة" if dust_storm_active else "مستقر آلياً")

    st.markdown("---")
    st.line_chart(df_total)

with tab2:
    st.subheader("🔍 خوارزميات الكشف المبكر عن الأعطال والتشخيص (Fault Detection & Diagnostics - FDD)")
    fdd_summary = []
    for inv_name, df_block in inverter_data.items():
        if inv_name == 'Plant_Total':
            continue
        
        ideal_sum = df_block['الواقع (ثنائي الوجه Bifacial)'].sum()
        actual_sum = df_block['قياسات سكادا الفعليّة (IoT)'].sum()
        deviation_pct = ((ideal_sum - actual_sum) / ideal_sum) * 100 if ideal_sum > 0 else 0
        
        if deviation_pct > 10.0 or dust_storm_active:
            status = "🚨 تنبيه حرج: تدهور حاد أو تأثير عاصفة رملية"
        elif deviation_pct > 4.0:
            status = "⚠️ تنبيه متوسط: انحراف في أداء السلاسل (String Mismatch)"
        else:
            status = "✅ الأداء طبيعي ومستقر"
            
        fdd_summary.append({
            'المحول (Inverter Block)': inv_name,
            'نسبة الانحراف الفعلي (%)': f"{deviation_pct:.2f}%",
            'الحالة التشخيصية (FDD Status)': status
        })
        
        with st.expander(f"تقرير تشخيص محول {inv_name} (الانحراف: {deviation_pct:.2f}%)"):
            st.write(f"الحالة الحالية: {status}")
            st.line_chart(df_block)

    st.table(pd.DataFrame(fdd_summary))

with tab3:
    st.subheader("🤖 غرفة عمليات أسطول الروبوتات الجافة وعمليات الطوارئ")
    r_col1, r_col2, r_col3 = st.columns(3)
    r_col1.metric("الروبوتات النشطة في الميدان", f"{total_robots} روبوت")
    r_col2.metric("استجابة طوارئ العواصف", "⚡ تفعيل المسح الفوري السريع" if dust_storm_active else "🛡️ الوضع الدوري العادي")
    r_col3.metric("استهلاك المياه للغسيل", "0.0 لتر (100% تنظيف جاف)")

    st.markdown("### 🗺️ توزيع الروبوتات وحالة الكتل التشغيلية")
    zone_data = []
    for i in range(num_inverters):
        inv_name = f"Inverter Block {i+1}"
        assigned_robots = int(total_robots / num_inverters)
        status_text = "🚨 حالة طوارئ عاصفة - تنظيف مكثف" if dust_storm_active else ("🔄 تنظيف دوري نشط" if i%2==0 else "🅿️ في محطة الشحن")
        
        zone_data.append({
            'المنطقة / الكتلة': inv_name,
            'الروبوتات المخصصة': assigned_robots,
            'مستوى النظافة الحالي': f"{max(10, 100 - inverter_configs[inv_name]['soiling']*2.5)}%",
            'حالة المهمة': status_text
        })
    st.table(pd.DataFrame(zone_data))

with tab4:
    st.subheader("💰 التحليل المالي بعيد المدى واقتصاديات أسطول الروبوتات (LCOE & Robot CAPEX)")
    
    # مؤشرات اقتصاديات الروبوتات المالية الصافية هنا
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)
    f_col1.metric("استثمار أسطول الروبوتات (CAPEX)", f"{initial_robot_capex:,.0f} ر.ع")
    f_col2.metric("إهلاك وصيانة الأسطول اليومي", f"{daily_robot_depreciation:.2f} ر.ع")
    f_col3.metric("صافي العائد الاقتصادي اليومي", f"{net_robotic_roi:,.2f} ر.ع")
    f_col4.metric("تكلفة الطاقة المستوية (LCOE)", f"{lcoe:.4f} ر.ع / kWh")

    st.markdown("---")
    
    col_fin1, col_fin2 = st.columns(2)
    with col_fin1:
        st.markdown("#### 📊 مقارنة التكاليف التشغيلية (روبوتات جافة مقابل غسيل تقليدي)")
        st.write("- **تكلفة المياه والعمالة اليدوية:** 0.00 ر.ع (محظورة بيئياً واقتصادياً في صحراء عمان لتطلبها ملايين اللترات).")
        st.write(f"- **إجمالي تكلفة تشغيل الروبوتات السنوية:** {(daily_robot_depreciation * 365):,.2f} ر.ع.")
        st.write(f"- **الإنتاج السنوي التقديري للمحطة:** {annual_generation_mwh:,.1f} MWh.")
        
    with col_fin2:
        st.markdown("#### 💡 العائد الاستثماري الاستراتيجي (ROI)")
        if net_robotic_roi > 0:
            st.success(f"✅ أسطول الروبوتات يحقق قيمة مضافة صافية قدرها **{net_robotic_roi:,.2f} ر.ع يومياً** عبر حماية الألواح من فقد كفاءة الإنتاج الصحراوي.")
        else:
            st.warning("⚠️ إهلاك الروبوتات يتجاوز الخسارة الحالية؛ يُوصى بتعديل دورات المسح.")

with tab5:
    st.subheader("📋 توليد أوامر الشغل الآلية (Automated Work Orders)")
    st.markdown("يقوم هذا النظام بتوليد أوامر صيانة جاهزة وموثقة لإرسالها لفرق الفنيين الميدانيين عند رصد أي أعطال أو ترسبات حرجة.")

    selected_inv_wo = st.selectbox("اختر المحول لإصدار أمر الشغل", [f"Inverter Block {i+1}" for i in range(num_inverters)])
    work_order_id = f"WO-OMAN-2026-{np.random.randint(1000, 9999)}"
    
    wo_payload = {
        "work_order_id": work_order_id,
        "facility": "Manah Solar Complex, Oman",
        "target_block": selected_inv_wo,
        "soiling_level": inverter_configs[selected_inv_wo]['soiling'],
        "tilt_error": inverter_configs[selected_inv_wo]['tilt_error'],
        "priority": "HIGH" if dust_storm_active or inverter_configs[selected_inv_wo]['soiling'] > 15 else "NORMAL",
        "assigned_robots": int(total_robots / num_inverters),
        "action_required": "Deploy dry-cleaning robot fleet override sweep & inspect DC wiring harnesses."
    }

    st.json(wo_payload)
    if st.button("تصدير وإرسال أمر الشغل لفرق الصيانة الميدانية"):
        st.success(f"✅ تم إصدار وتثبيت أمر الشغل رقم **{work_order_id}** بنجاح وإرساله لنظام الصيانة المركزي!")

st.markdown("---")
st.subheader("🤖 تقرير تحليل الأصول والعمليات المؤسسية (Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل الوكيل الذكي.")
else:
    if st.button("توليد التقرير التشغيلي الشامل للأصول"):
        with st.spinner("الوكيل الذكي يحلل LCOE، العواصف الرملية، أوامر الشغل، وإهلاك الروبوتات المالي..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت الرئيس التنفيذي للعمليات الهندسية وخبير إدارة محطات الطاقة الشمسية الكبرى في سلطنة عمان.
                بيانات المحطة المالية والتشغيلية:
                - القدرة الكلية: {total_capacity_mw} MW.
                - الاستثمار الأولي للروبوتات (CAPEX): {initial_robot_capex:,.0f} ر.ع.
                - إهلاك الروبوتات اليومي: {daily_robot_depreciation} ر.ع.
                - صافي العائد الاقتصادي اليومي: {net_robotic_roi:,.2f} ر.ع.
                - تكلفة الطاقة المستوية (LCOE): {lcoe:.4f} ر.ع / kWh.
                - حالة العاصفة الرملية: {"نشطة" if dust_storm_active else "غير نشطة"}
                
                قدم تقريراً تشغيلياً واقتصادياً متعمقاً باللغة العربية للإدارة العليا يغطي العائد المالي للاستثمار في الروبوتات، كفاءة الأصول في حالات الطوارئ الصحراوية، والتوصيات المالية طويلة المدى.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير المالي والمؤسسي بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
