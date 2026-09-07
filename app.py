import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
from google import genai

st.set_page_config(page_title="RE-OPT: Enterprise Digital Twin Dashboard", layout="wide")

st.title("⚡ RE-OPT: Enterprise Renewable Energy Digital Twin & O&M Platform")
st.markdown("منصة التوأم الرقمي المؤسسي - التحكم المستقل والرسوم البيانية المخصصة لكل محول (Inverter).")

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

st.sidebar.header("إعدادات البنية المؤسسية والطبولوجيا")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity = st.sidebar.slider("إجمالي قدرة المحطة (kW)", min_value=30.0, max_value=300.0, value=90.0, step=30.0)
num_inverters = st.sidebar.selectbox("عدد محولات الطاقة (Inverters)", [2, 3, 4], index=1)

st.sidebar.subheader("بيانات الموقع الحي (مسقط)")
live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind), step=0.5)
live_wind = live_wind_kmh / 3.6

albedo = st.sidebar.slider("معامل الانعكاس والأرضية (Albedo)", min_value=0.1, max_value=1.0, value=0.35, step=0.05)
tariff = st.sidebar.number_input("تعرفة الكهرباء المؤسسية (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

# **التحكم المستقل لكل محول مع الحفاظ على الحالة (State Management)**
st.sidebar.subheader("التحكم المستقل للمحولات (Individual Inverter Settings)")
inverter_configs = {}

for i in range(num_inverters):
    inv_name = f"Inverter Block {i+1}"
    with st.sidebar.expander(f"إعدادات تشغيل {inv_name}", expanded=(i==0)):
        inv_soiling = st.slider(f"فقدان الغبار (%) - {inv_name}", min_value=0.0, max_value=40.0, value=float(10.0 + (i * 5.0)), step=1.0, key=f"soiling_inv_{i}")
        inv_tilt_err = st.slider(f"خطأ الميل (°) - {inv_name}", min_value=0.0, max_value=15.0, value=float(i * 2.0), step=1.0, key=f"tilt_inv_{i}")
        inverter_configs[inv_name] = {'soiling': inv_soiling, 'tilt_error': inv_tilt_err}

@st.cache_data
def run_independent_inverter_simulation(total_cap, n_inv, configs, alb, t_amb, wind):
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
    
    for i in range(n_inv):
        inv_name = f"Inverter Block {i+1}"
        cfg = configs[inv_name]
        
        base_power = (ghi / peak_ghi) * block_capacity * albedo_factor
        base_power = base_power.clip(lower=0)
        ideal_power = base_power * temp_factor
        
        total_degradation = cfg['soiling'] + (cfg['tilt_error'] * 0.5)
        actual_factor = max(0.0, 1.0 - (total_degradation / 100.0))
        actual_power = ideal_power * actual_factor
        
        simulation_results[inv_name] = pd.DataFrame({
            'التوأم الرقمي (المرجع المثالي)': ideal_power,
            'الواقع التشغيلي (الفعلي للمحول)': actual_power
        }, index=times)
        
    return simulation_results

inverter_data = run_independent_inverter_simulation(total_capacity, num_inverters, inverter_configs, albedo, live_temp, live_wind)

st.subheader("📊 الرسوم البيانية للتوأم الرقمي المستقل لكل محول طاقة")

total_plant_loss_kwh = 0

for inv_name, df_block in inverter_data.items():
    st.markdown(f"**🔹 مسار أداء وتحليلات {inv_name}**")
    
    ideal_sum = df_block['التوأم الرقمي (المرجع المثالي)'].sum()
    actual_sum = df_block['الواقع التشغيلي (الفعلي للمحول)'].sum()
    block_loss = ideal_sum - actual_sum
    total_plant_loss_kwh += block_loss
    block_financial_loss = block_loss * tariff
    
    col1, col2, col3 = st.columns(3)
    col1.metric(f"فقد الطاقة المفقودة ({inv_name})", f"{block_loss:.2f} kWh")
    col2.metric(f"الخسارة المالية ({inv_name})", f"{block_financial_loss:.3f} ر.ع")
    col3.metric(f"الإعدادات النشطة", f"غبار: {inverter_configs[inv_name]['soiling']}% | ميل: {inverter_configs[inv_name]['tilt_error']}°")
    
    # رسوم بيانية منفصلة ومستقلة لكل محول تحتوي على التوأم الرقمي الخاص به
    st.line_chart(df_block)
    st.markdown("---")

total_plant_financial_loss = total_plant_loss_kwh * tariff

st.subheader("🤖 تقرير تحليل الأداء المؤسسي الشامل (Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل الوكيل الذكي.")
else:
    if st.button("توليد التقرير التحليلي الموحد للمحولات المستقلة"):
        with st.spinner("الوكيل الذكي يحلل سلوك الكتل التشغيلية المستقلة..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت مدير هندسة الأصول التشغيلية وخبير الطاقة الشمسية.
                بيانات إعدادات المحولات المستقلة الحالية:
                {inverter_configs}
                - إجمالي فقد الطاقة للمحطة: {total_plant_loss_kwh:.2f} kWh
                - إجمالي الخسارة المالية للمحطة: {total_plant_financial_loss:.3f} ر.ع
                - ظروف مسقط: حرارة {live_temp}°C، رياح {live_wind_kmh} km/h.
                
                قدم تقريراً تشغيلياً واحترافياً باللغة العربية يحلل الأداء المستقل لكل محول، ويقارن تأثير الفروقات الفردية في الإعدادات، ويقدم التوصيات الهندسية لإدارة الصيانة.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
