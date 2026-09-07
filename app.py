import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
from google import genai

st.set_page_config(page_title="RE-OPT: Enterprise Digital Twin Dashboard", layout="wide")

st.title("⚡ RE-OPT: Enterprise Renewable Energy Digital Twin & O&M Platform")
st.markdown("منصة التوأم الرقمي المؤسسي وإدارة أصول الطاقة الشمسية - قطاع العمليات التشغيلية.")

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

st.sidebar.header("إعدادات لوحة التحكم المؤسسية")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
rated_capacity = st.sidebar.slider("قدرة المحطة الإجمالية (kW)", min_value=10.0, max_value=500.0, value=50.0, step=10.0)

st.sidebar.subheader("بيانات الموقع الحي (مسقط)")
live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind), step=0.5)
live_wind = live_wind_kmh / 3.6

st.sidebar.subheader("معايير أداء الأصول والصيانة")
days_since_cleaning = st.sidebar.slider("الأيام المنقضيّة منذ آخر عملية تنظيف", min_value=1, max_value=90, value=12, step=1)
max_soiling_limit = 40.0
soiling_loss = max_soiling_limit * (1.0 - np.exp(-0.04 * days_since_cleaning))

albedo = st.sidebar.slider("معامل الانعكاس والأرضية (Albedo)", min_value=0.1, max_value=1.0, value=0.35, step=0.05)
tilt_error = st.sidebar.slider("خطأ زاوية الميل (Degrees °)", min_value=0.0, max_value=90.0, value=2.0, step=1.0)
tariff = st.sidebar.number_input("تعرفة الكهرباء المؤسسية (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

st.sidebar.subheader("اقتصاديات الصيانة والتشغيل (O&M)")
cleaning_cost = st.sidebar.number_input("تكلفة عقد التنظيف الميداني (ر.ع)", min_value=10.0, max_value=500.0, value=50.0, step=5.0)

@st.cache_data
def run_enterprise_simulation(capacity, soiling_pct, alb, tilt_err, t_amb, wind):
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
        'الإنتاج المثالي (مرجع المحطة)': ideal_thermal_power,
        'الإنتاج الفعلي (بعد التدهور والغبار)': actual_power
    }, index=times)
    
    return results

df_results = run_enterprise_simulation(rated_capacity, soiling_loss, albedo, tilt_error, live_temp, live_wind)

loss_kwh = (df_results['الإنتاج المثالي (مرجع المحطة)'] - df_results['الإنتاج الفعلي (بعد التدهور والغبار)']).sum()
daily_financial_loss = loss_kwh * tariff
accumulated_loss = daily_financial_loss * days_since_cleaning

# خوارزمية التنبؤ بموعد الصيانة المستقبلي (Predictive Forecasting Loop)
predicted_day_to_threshold = days_since_cleaning
temp_accumulated = accumulated_loss
simulated_days_ahead = 0

while temp_accumulated < cleaning_cost and simulated_days_ahead < 60:
    simulated_days_ahead += 1
    future_soiling = max_soiling_limit * (1.0 - np.exp(-0.04 * (days_since_cleaning + simulated_days_ahead)))
    future_loss_kwh = loss_kwh * (future_soiling / max(1.0, soiling_loss))
    temp_accumulated += future_loss_kwh * tariff

optimal_cleaning_window = days_since_cleaning + simulated_days_ahead

col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي الفقد اليومي", f"{loss_kwh:.2f} kWh")
col2.metric("الخسارة المالية اليومية", f"{daily_financial_loss:.3f} ر.ع")
col3.metric("الخسارة المتراكمة الحالية", f"{accumulated_loss:.2f} ر.ع")
col4.metric("التنبؤ: نافذة التنظيف المثلى", f"بعد {optimal_cleaning_window} يوماً")

st.markdown("---")

if accumulated_loss >= cleaning_cost:
    st.error(f"🚨 **إنذار تشغيلي حرج:** الخسائر المتراكمة الحالية ({accumulated_loss:.2f} ر.ع) تجاوزت تكلفة الصيانة المعتمدة. **يجب إصدار أمر عمل فوري لفرق الصيانة.**")
else:
    st.success(f"✅ **كفاءة الأصول مستقرة:** الخسائر المتراكمة دون الحد الحرج. نموذج التنبؤ يوصي بجدولة التنظيف الميداني القادم خلال **{simulated_days_ahead} يوماً** (في اليوم {optimal_cleaning_window} تقريباً).")

st.subheader("تحليل منحنى محاكاة الأداء اليومي للأصول")
st.line_chart(df_results)

st.subheader("🤖 تقرير تحليل الأصول والجدولة التنبؤية (Interخصائص المؤسسات & Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل الوكيل الذكي المؤسسي.")
else:
    if st.button("توليد تقرير العمليات التشغيلية والجدولة التنبؤية"):
        with st.spinner("الوكيل الذكي يعالج خوارزميات التنبؤ المسبق للغبار وجدولة الصيانة المؤسسية..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت مدير هندسة الأصول التشغيلية وخبير استراتيجي في إدارة قطاع الطاقة.
                بيانات الأصول المؤسسية الحالية:
                - قدرة المحطة: {rated_capacity} kW
                - الأيام منذ آخر صيانة: {days_since_cleaning} يوماً
                - نسبة الفقد الحالي بسبب الغبار (غير خطي): {soiling_loss:.1f}%
                - الخسارة المالية اليومية: {daily_financial_loss:.3f} ر.ع
                - الخسارة المتراكمة الحالية: {accumulated_loss:.2f} ر.ع
                - تكلفة عقد التنظيف: {cleaning_cost} ر.ع
                - التنبؤ الذكي: نافذة التنظيف الموصى بها خلال {simulated_days_ahead} يوماً إضافياً (الإجمالي اليوم {optimal_cleaning_window}).
                - الظروف المناخية الحية: حرارة {live_temp}°C، رياح {live_wind_kmh} km/h.
                
                قدم تقريراً تشغيلياً واحترافياً باللغة العربية موجهًا للإدارة العليا في الشركة، يتضمن:
                1. تقييم كفاءة الأصول الحالية في بيئة مسقط وتحليل سرعة تراكم الأتربة.
                2. تفصيل نموذج التنبؤ المالي وتحديد الجدوى الاقتصادية الدقيقة لقرار تأجيل أو تقديم الصيانة.
                3. التوصيات التنفيذية وجدولة أوامر العمل لفرق الصيانة الميدانية.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير المؤسسي بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
