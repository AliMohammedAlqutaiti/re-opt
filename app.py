import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
from google import genai

st.set_page_config(page_title="RE-OPT: Enterprise Industrial Digital Twin", layout="wide")

st.title("⚡ RE-OPT: Manah Enterprise Digital Twin & AI Operations Center")
st.markdown("منصة التوأم الرقمي المؤسسي - مقارنة الألواح الأحادية وثنائية الوجه، تشخيص الأعطال (FDD)، وأسطول الروبوتات.")

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

st.sidebar.header("إعدادات البنية المؤسسية والتشغيلية")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=500.0, value=100.0, step=50.0) * 1000 
num_inverters = st.sidebar.selectbox("عدد محولات الطاقة (Inverter Blocks)", [2, 4, 6], index=1)

scada_mode = st.sidebar.toggle("تفعيل الربط الحي مع محطة SCADA (IoT Stream)", value=True)

if scada_mode:
    live_temp = api_temp
    live_wind = api_wind
else:
    live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
    live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind*3.6), step=0.5)
    live_wind = live_wind_kmh / 3.6

# إعدادات التكنولوجيا (أحادية مقابل ثنائية الوجه)
st.sidebar.subheader("تكنولوجيا الألواح (Technology Mix)")
technology_type = st.sidebar.selectbox("نوع ألواح المحطة الرئيسية", ["ثنائية الوجه (Bifacial)", "أحادية الوجه (Mono-facial)", "هجين (مزيج بين النوعين)"])

albedo = st.sidebar.slider("معامل الانعكاس الأرضي (Albedo)", min_value=0.1, max_value=0.8, value=0.40, step=0.05)
bifaciality_factor = st.sidebar.slider("معامل ثنائية الوجه للألواح (%)", min_value=60.0, max_value=85.0, value=70.0, step=5.0) / 100.0

tariff = st.sidebar.number_input("تعرفة الكهرباء المؤسسية (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

# إدارة أسطول الروبوتات
st.sidebar.subheader("إدارة أسطول الروبوتات الجافة (1,800 Robot Fleet)")
total_robots = st.sidebar.number_input("إجمالي الروبوتات النشطة", min_value=500, max_value=3000, value=1800, step=100)
daily_robot_depreciation = st.sidebar.number_input("إهلاك الصيانة اليومي للأسطول (ر.ع)", min_value=10.0, max_value=300.0, value=45.0, step=5.0)

st.sidebar.subheader("التحكم المستقل لكتل المحولات (Inverter Blocks)")
inverter_configs = {}
for i in range(num_inverters):
    inv_name = f"Inverter Block {i+1}"
    with st.sidebar.expander(f"إعدادات تشغيل {inv_name}", expanded=(i==0)):
        inv_soiling = st.slider(f"تراكم الغبار (%) - {inv_name}", min_value=0.0, max_value=25.0, value=float(2.0 + (i * 1.5)), step=0.5, key=f"soiling_inv_{i}")
        inv_tilt_err = st.slider(f"خطأ الميل (°) - {inv_name}", min_value=0.0, max_value=10.0, value=float(i * 0.8), step=0.5, key=f"tilt_inv_{i}")
        inverter_configs[inv_name] = {'soiling': inv_soiling, 'tilt_error': inv_tilt_err}

@st.cache_data
def run_enterprise_simulation(total_cap, n_inv, configs, tech_mode, alb, bif_factor, t_amb, wind, is_live):
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
        
        # القدرة الأساسية للنوعين
        mono_base = base_power * temp_factor
        bif_gain = 1.0 + (alb * bif_factor * 0.18)
        bif_base = base_power * temp_factor * bif_gain
        
        # تطبيق عوامل التدهور والترسبات على كلا النوعين بالتساوي
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

inverter_data = run_enterprise_simulation(total_capacity, num_inverters, inverter_configs, technology_type, albedo, bifaciality_factor, live_temp, live_wind, scada_mode)

df_total = inverter_data['Plant_Total']
total_plant_loss_kwh = (df_total['إجمالي الواقع (ثنائي الوجه)'] - df_total['إجمالي قياسات سكادا الفعلية']).sum()
daily_financial_loss = max(0.0, abs(total_plant_loss_kwh) * tariff)
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

tab1, tab2, tab3 = st.tabs(["📈 المقارنة بين الأحادية وثنائية الوجه", "🔍 التشخيص الذكي للأعطال (FDD)", "🤖 غرفة عمليات أسطول الروبوتات"])

with tab1:
    st.subheader(f"📈 مقارنة إنتاجية المحطة ({technology_type})")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("نوع التكنولوجيا المعتمدة", technology_type)
    c2.metric("حجم الفارق الإنتاجي", f"{abs(total_plant_loss_kwh):,.1f} kWh")
    c3.metric("تكلفة إهلاك الروبوتات", f"{daily_robot_depreciation:.2f} ر.ع")
    c4.metric("صافي العائد الاقتصادي", f"{net_robotic_roi:,.2f} ر.ع")

    st.markdown("---")
    st.markdown("**مقارنة تفصيلية بين أداء الألواح أحادية الوجه (Mono)، ثنائية الوجه (Bifacial)، والواقع الفعلي للسكادا:**")
    st.line_chart(df_total)

with tab2:
    st.subheader("🔍 خوارزميات الكشف المبكر عن الأعطال والتشخيص (Fault Detection & Diagnostics - FDD)")
    st.markdown("مقارنة الإنتاج الفعلي للسكادا مع النموذج الواقعي لتقييم الانحرافات واكتشاف الأعطال الخفية في الكتل.")

    fdd_summary = []
    for inv_name, df_block in inverter_data.items():
        if inv_name == 'Plant_Total':
            continue
        
        ideal_sum = df_block['الواقع (ثنائي الوجه Bifacial)'].sum()
        actual_sum = df_block['قياسات سكادا الفعليّة (IoT)'].sum()
        deviation_pct = ((ideal_sum - actual_sum) / ideal_sum) * 100 if ideal_sum > 0 else 0
        
        if deviation_pct > 8.0:
            status = "🚨 تنبيه حرج: تدهور حاد / ترسبات غبار غير معتادة"
        elif deviation_pct > 4.0:
            status = "⚠️ تنبيه متوسط: انحراف طفيف في أداء سلسلة (String Mismatch)"
        else:
            status = "✅ الأداء طبيعي ومستقر ضمن الحدود التشغيلية"
            
        fdd_summary.append({
            'المحول (Inverter Block)': inv_name,
            'نسبة الانحراف الفعلي (%)': f"{deviation_pct:.2f}%",
            'الحالة التشخيصية (FDD Status)': status
        })
        
        with st.expander(f"تقرير تشخيص محول {inv_name} (الانحراف: {deviation_pct:.2f}%)"):
            st.write(f"الحالة الحالية: {status}")
            st.line_chart(df_block[['الواقع (أحادى الوجه Mono)', 'الواقع (ثنائي الوجه Bifacial)', 'قياسات سكادا الفعليّة (IoT)']])

    st.table(pd.DataFrame(fdd_summary))

with tab3:
    st.subheader("🤖 لوحة تحكم ومراقبة أسطول الروبوتات الآلية (Robotic Mission Control)")
    st.markdown(f"إدارة ومتابعة **{total_robots} روبوت** لتنظيف الألواح جافاً عبر كتل المحطة.")

    r_col1, r_col2, r_col3 = st.columns(3)
    r_col1.metric("الروبوتات النشطة في الخدمة", f"{int(total_robots * 0.95)} روبوت")
    r_col2.metric("الروبوتات قيد الشحن/الصيانة", f"{int(total_robots * 0.05)} روبوت")
    r_col3.metric("معدل استهلاك المياه", "0.0 لتر (100% تنظيف جاف)")

    st.markdown("### 🗺️ حالة انتشار الروبوتات في كتل المحطة (Robotic Zone Status)")
    
    zone_data = []
    for i in range(num_inverters):
        inv_name = f"Inverter Block {i+1}"
        assigned_robots = int(total_robots / num_inverters)
        battery_health = f"{92 - (i*3)}%"
        cleaning_progress = f"{100 - (inverter_configs[inv_name]['soiling'] * 3)}%"
        
        zone_data.append({
            'المنطقة / الكتلة': inv_name,
            'عدد الروبوتات المخصصة': assigned_robots,
            'صحة بطاريات الأسطول': battery_health,
            'مستوى النظافة الحالي': cleaning_progress,
            'حالة المهمة': '🔄 تنظيف دوري نشط' if i%2==0 else '🅿️ في محطة الشحن'
        })
        
    st.table(pd.DataFrame(zone_data))

st.markdown("---")
st.subheader("🤖 تقرير تحليل الأصول والعمليات المؤسسية (Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل الوكيل الذكي.")
else:
    if st.button("توليد التقرير التشغيلي الشامل للأصول"):
        with st.spinner("الوكيل الذكي يحلل أداء الألواح الأحادية والثنائية، فحص FDD، وأسطول الروبوتات..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت الرئيس التنفيذي للعمليات الهندسية وخبير إدارة محطات الطاقة الشمسية الكبرى.
                بيانات المحطة الحالية:
                - نوع تكنولوجيا الألواح: {technology_type}
                - القدرة الكلية: {total_capacity/1000} MW على {num_inverters} محولات.
                - أسطول الروبوتات: {total_robots} روبوت تنظيف جاف.
                - حجم الفارق الإنتاجي: {abs(total_plant_loss_kwh):,.1f} kWh.
                - صافي العائد بعد إهلاك الروبوتات: {net_robotic_roi:,.2f} ر.ع.
                
                قدم تقريراً تشغيلياً واحترافياً متعمقاً باللغة العربية للإدارة العليا يغطي:
                1. مقارنة كفاءة وإنتاجية الألواح أحادية الوجه مقابل ثنائية الوجه في بيئة مسقط.
                2. نتائج الكشف عن الأعطال (FDD) وكفاءة كتل المحولات.
                3. تقييم كفاءة عمليات أسطول الروبوتات الجافة وتوصيات الصيانة الميدانية.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير المؤسسي الشامل بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
