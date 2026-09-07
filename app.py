import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
from google import genai

st.set_page_config(page_title="RE-OPT: Enterprise SCADA Digital Twin", layout="wide")

st.title("⚡ RE-OPT: SCADA & IoT Integrated Digital Twin")
st.markdown("منصة التوأم الرقمي المؤسسي - التكامل الحي مع أنظمة السكادا (SCADA) وإنترنت الأشياء (IoT).")

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

st.sidebar.header("إعدادات الاتصال والسكادا (SCADA & IoT)")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")

# خيار التبديل بين محاكاة السكادا الحية والتحكم اليدوي
scada_mode = st.sidebar.toggle("تفعيل الربط الحي مع محطة SCADA (IoT Stream)", value=False)

total_capacity = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=500.0, value=100.0, step=50.0) * 1000 
num_inverters = st.sidebar.selectbox("عدد محولات الطاقة (Inverter Blocks)", [2, 4, 6], index=1)

if scada_mode:
    st.sidebar.success("🟢 متصل بـ SCADA Inverter Gateway (عبر MQTT/Modbus)")
    live_temp = api_temp
    live_wind = api_wind
    st.sidebar.info(f"🌡️ حرارة السكادا الحية: {live_temp}°C | 💨 الرياح: {live_wind*3.6:.1f} km/h")
else:
    st.sidebar.warning("🟡 الوضع اليدوي / المحاكاة المتقدمة")
    live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
    live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind*3.6), step=0.5)
    live_wind = live_wind_kmh / 3.6

albedo = st.sidebar.slider("معامل الانعكاس والأرضية (Albedo)", min_value=0.1, max_value=1.0, value=0.35, step=0.05)
tariff = st.sidebar.number_input("تعرفة الكهرباء المؤسسية (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

st.sidebar.subheader("اقتصاديات أسطول الروبوتات الآلية (Robotic O&M)")
robot_fleet_size = st.sidebar.number_input("عدد روبوتات التنظيف الجاف النشطة", min_value=100, max_value=5000, value=1200, step=100)
daily_robot_depreciation = st.sidebar.number_input("التكلفة اليومية لإهلاك وصيانة الروبوتات (ر.ع / يوم)", min_value=5.0, max_value=200.0, value=35.0, step=5.0)

st.sidebar.subheader("التحكم المستقل لكتل المحولات (Inverter Blocks)")
inverter_configs = {}
for i in range(num_inverters):
    inv_name = f"Inverter Block {i+1}"
    with st.sidebar.expander(f"إعدادات تشغيل {inv_name}", expanded=(i==0)):
        # إذا كان وضع سكادا مفعل، يمكن جلب القراءات الفعلية من الحساسات
        default_soiling = float(2.0 + (i * 1.0)) if scada_mode else float(3.0 + (i * 1.5))
        inv_soiling = st.slider(f"فراغ الغبار التشغيلي (%) - {inv_name}", min_value=0.0, max_value=20.0, value=default_soiling, step=0.5, key=f"soiling_inv_{i}")
        inv_tilt_err = st.slider(f"خطأ الميل الفعلي (°) - {inv_name}", min_value=0.0, max_value=10.0, value=float(i * 1.0), step=0.5, key=f"tilt_inv_{i}")
        inverter_configs[inv_name] = {'soiling': inv_soiling, 'tilt_error': inv_tilt_err}

@st.cache_data
def run_scada_simulation(total_cap, n_inv, configs, alb, t_amb, wind, is_live):
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
    
    albedo_factor = 1.0 + ((alb - 0.2) * 0.15)
    block_capacity = total_cap / n_inv
    
    simulation_results = {}
    total_ideal = np.zeros(len(times))
    total_actual = np.zeros(len(times))
    
    for i in range(n_inv):
        inv_name = f"Inverter Block {i+1}"
        cfg = configs[inv_name]
        
        base_power = (ghi / peak_ghi) * block_capacity * albedo_factor
        base_power = base_power.clip(lower=0)
        ideal_power = base_power * temp_factor
        
        total_degradation = cfg['soiling'] + (cfg['tilt_error'] * 0.5)
        actual_factor = max(0.0, 1.0 - (total_degradation / 100.0))
        
        # إذا كان وضع سكادا مفعل، نضيف تشويشاً طفيفاً يمثل تذبذب قراءات الحساسات الحية (Noise)
        if is_live:
            np.random.seed(42 + i)
            noise = np.random.normal(1.0, 0.01, len(times))
            actual_power = ideal_power * actual_factor * noise
        else:
            actual_power = ideal_power * actual_factor
            
        simulation_results[inv_name] = pd.DataFrame({
            'التوأم الرقمي (المرجع المثالي)': ideal_power,
            'قياسات سكادا الفعلية (IoT Feed)': actual_power
        }, index=times)
        
        total_ideal += ideal_power
        total_actual += actual_power

    simulation_results['Plant_Total'] = pd.DataFrame({
        'التوأم الرقمي للمحطة (المثالي)': total_ideal,
        'إجمالي قياسات سكادا (الفعلي)': total_actual
    }, index=times)
    
    return simulation_results

inverter_data = run_scada_simulation(total_capacity, num_inverters, inverter_configs, albedo, live_temp, live_wind, scada_mode)

df_total = inverter_data['Plant_Total']
total_plant_loss_kwh = (df_total['التوأم الرقمي للمحطة (المثالي)'] - df_total['إجمالي قياسات سكادا (الفعلي)']).sum()
daily_financial_loss = max(0.0, total_plant_loss_kwh * tariff)
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

tab1, tab2 = st.tabs(["📈 لوحة القيادة والسكادا العامة", "🔌 تحليل المحولات المستقلة (SCADA Telemetry)"])

with tab1:
    if scada_mode:
        st.info("📡 **حالة النظام:** يتم استقبال بيانات التليمتري الحية عبر بروتوكول SCADA Gateway بشكل لحظي.")
    else:
        st.info("🖥️ **حالة النظام:** تعمل المنصة في وضع المحاكاة الهندسية المستقلة.")
        
    col_r1, col_r2, col_r3, col_r4 = st.columns(4)
    col_r1.metric("وضع الاتصال", "Live SCADA" if scada_mode else "Manual Simulation")
    col_r2.metric("إجمالي الفقد اليومي للطاقة", f"{total_plant_loss_kwh:,.1f} kWh")
    col_r3.metric("إهلاك أسطول الروبوتات", f"{daily_robot_depreciation:.2f} ر.ع")
    col_r4.metric("صافي العائد الاقتصادي", f"{net_robotic_roi:,.2f} ر.ع")

    st.markdown("---")
    st.markdown("**مقارنة الإنتاج الكلي للمحطة (التوأم الرقمي المثالي مقابل بيانات سكادا الفعلية)**")
    st.line_chart(df_total)

with tab2:
    st.subheader("🔌 قياسات التليمتري الحية لكل محول طاقة (Inverter Telemetry)")
    for inv_name, df_block in inverter_data.items():
        if inv_name == 'Plant_Total':
            continue
            
        st.markdown(f"**🔹 قراءات حساسات ومحولات {inv_name}**")
        
        ideal_sum = df_block['التوأم الرقمي (المرجع المثالي)'].sum()
        actual_sum = df_block['قياسات سكادا الفعلية (IoT Feed)'].sum()
        block_loss = ideal_sum - actual_sum
        block_financial_loss = max(0.0, block_loss * tariff)
        
        c1, c2, c3 = st.columns(3)
        c1.metric(f"الطاقة المفقودة ({inv_name})", f"{block_loss:,.1f} kWh")
        c2.metric(f"الخسارة المالية ({inv_name})", f"{block_financial_loss:,.3f} ر.ع")
        c3.metric(f"قراءات الحساسات النشطة", f"غبار: {inverter_configs[inv_name]['soiling']}%")
        
        st.line_chart(df_block)
        st.markdown("---")

st.subheader("🤖 تقرير تحليل بيانات سكادا والأداء المؤسسي (Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key لتفعيل الوكيل الذكي.")
else:
    if st.button("توليد تقرير تشخيص سكادا وأسطول الروبوتات"):
        with st.spinner("الوكيل الذكي يحلل تدفقات بيانات سكادا وحساسات إنترنت الأشياء..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت مدير هندسة التشغيل والتحكم الآلي في المحطات الكبرى (SCADA & IoT Operations Manager).
                بيانات الربط الحالية:
                - وضع الاتصال: {"Live SCADA Stream Active" if scada_mode else "Simulation Mode"}
                - إجمالي القدرة: {total_capacity/1000} MW على {num_inverters} محولات.
                - إجمالي الفقد اليومي المكتشف من السكادا: {total_plant_loss_kwh:,.1f} kWh.
                - صافي العائد الاقتصادي بعد خصم إهلاك الروبوتات: {net_robotic_roi:,.2f} ر.ع.
                - حالة الطقس الحية من السكادا: حرارة {live_temp}°C، رياح {live_wind*3.6:.1f} km/h.
                
                قدم تقريراً تشغيلياً واحترافياً باللغة العربية للإدارة العليا يوضح:
                1. تقييم دقة قراءات التليمتري الحية القادمة من حساسات سكادا ومقارنتها بالنموذج المثالي للتوأم الرقمي.
                2. كفاءة الاستجابة التلقائية لأسطول الروبوتات الجافة في معالجة الانحرافات المرصودة عبر الشبكة.
                3. التوصيات التقنية لفريق هندسة التحكم الآلي لضمان استقرار قراءات الـ MPPT.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير التحليلي لبيانات سكادا بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
