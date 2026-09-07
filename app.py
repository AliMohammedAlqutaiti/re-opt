import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
from google import genai

st.set_page_config(page_title="RE-OPT: Digital Twin Dashboard", layout="wide")

st.title("⚡ RE-OPT: AI-Powered Renewable Energy Digital Twin")
st.markdown("منصة التوأم الرقمي والوكيل الذكي المعتمد على Google Gemini لمراقبة وتشخيص أصول الطاقة الشمسية في مسقط.")

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

st.sidebar.header("إعدادات المحاكاة والبيانات الحية")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
rated_capacity = st.sidebar.slider("قدرة المحطة (kW)", min_value=5.0, max_value=50.0, value=10.0, step=5.0)

st.sidebar.subheader("التحكم في بيانات الطقس (مطابقة الهاتف)")
live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind), step=0.5)
live_wind = live_wind_kmh / 3.6

st.sidebar.subheader("عوامل الأداء والتشخيص المتقدمة")
# تحويل مدخلات الأيام إلى نسبة فقدان غير خطية للغبار
days_since_cleaning = st.sidebar.slider("الأيام منذ آخر تنظيف للألواح", min_value=1, max_value=90, value=15, step=1)
# معادلة تراكم الغبار غير الخطية (Asymptotic Soiling Curve)
max_soiling_limit = 40.0 # أقصى نسبة فقد ممكنة بدون تنظيف
soiling_loss = max_soiling_limit * (1.0 - np.exp(-0.04 * days_since_cleaning))

albedo = st.sidebar.slider("معامل الانعكاس والأرضية (Albedo)", min_value=0.1, max_value=1.0, value=0.35, step=0.05)
tilt_error = st.sidebar.slider("خطأ زاوية الميل (Degrees °)", min_value=0.0, max_value=90.0, value=5.0, step=1.0)

tariff = st.sidebar.number_input("تعرفة الكهرباء (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

@st.cache_data
def run_hybrid_simulation(capacity, soiling_pct, alb, tilt_err, t_amb, wind):
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
    base_power = (ghi / peak_ghi) * capacity * albedo_factor
    base_power = base_power.clip(lower=0)
    
    ideal_thermal_power = base_power * temp_factor
    
    total_degradation_pct = soiling_pct + (tilt_err * 0.5)
    actual_factor = max(0.0, 1.0 - (total_degradation_pct / 100.0))
    actual_power = ideal_thermal_power * actual_factor
    
    results = pd.DataFrame({
        'التوأم الرقمي (مع طقس مسقط والحرارة)': ideal_thermal_power,
        'الواقع التشغيلي (مع الغبار غير الخطي)': actual_power
    }, index=times)
    
    return results

df_results = run_hybrid_simulation(rated_capacity, soiling_loss, albedo, tilt_error, live_temp, live_wind)

loss_kwh = (df_results['التوأم الرقمي (مع طقس مسقط والحرارة)'] - df_results['الواقع التشغيلي (مع الغبار غير الخطي)']).sum()
financial_loss = loss_kwh * tariff

col1, col2, col3 = st.columns(3)
col1.metric("إجمالي الطاقة المفقودة اليوم", f"{loss_kwh:.2f} kWh")
col2.metric("الخسارة المالية التقديرية", f"{financial_loss:.3f} ر.ع")
col3.metric("نسبة فقدان الغبار المحسوبة", f"{soiling_loss:.1f}% (بعد {days_since_cleaning} يوم)")

st.markdown("---")
st.subheader("مقارنة الأداء: التوأم الهجين مقابل الواقع التشغيلي بالتراكم غير الخطي للغبار")
st.line_chart(df_results)

st.subheader("🤖 تقرير تحليل الوكيل الذكي (Interactions API & Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل تقرير الوكيل الذكي.")
else:
    if st.button("توليد التقرير التحليلي غير الخطي"):
        with st.spinner("الوكيل الذكي يحلل معدل تراكم الغبار والأثر المالي..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت وكيل ذكاء اصطناعي خبير في هندسة الطاقة الشمسية.
                بيانات المحاكاة الحالية:
                - الأيام منذ آخر تنظيف: {days_since_cleaning} يوماً
                - نسبة فقدان الغبار المحسوبة غير خطياً: {soiling_loss:.1f}%
                - درجة الحرارة المحيطة: {live_temp}°C | سرعة الرياح: {live_wind_kmh} km/h
                - قدرة المحطة: {rated_capacity} kW
                - الطاقة المفقودة اليوم: {loss_kwh:.2f} kWh
                - الخسارة المالية: {financial_loss:.3f} ريال عماني
                - التعرفة: {tariff} ر.ع/kWh
                
                قدم تقريراً تشغيلياً واحترافياً باللغة العربية يوضح تأثير تراكم الغبار غير الخطي عبر الزمن في بيئة مسقط، وتقييماً للأثر المالي، وتوصية بموعد التنظيف الأمثل.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
