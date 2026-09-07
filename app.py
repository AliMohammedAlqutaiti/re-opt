import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
from google import genai

st.set_page_config(page_title="RE-OPT: Enterprise Digital Twin Dashboard", layout="wide")

st.title("⚡ RE-OPT: Enterprise Renewable Energy Digital Twin & O&M Platform")
st.markdown("منصة التوأم الرقمي المؤسسي وإدارة أصول الطاقة الشمسية - طبولوجيا المحولات وسلاسل الألواح الموزعة.")

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
total_capacity = st.sidebar.slider("إجمالي قدرة المحطة (kW)", min_value=50.0, max_value=500.0, value=100.0, step=50.0)

st.sidebar.subheader("توزيع المحولات (Inverter Blocks)")
num_inverters = st.sidebar.selectbox("عدد محولات الطاقة (Inverters)", [2, 3, 4], index=0)

st.sidebar.subheader("بيانات الموقع الحي (مسقط)")
live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind), step=0.5)
live_wind = live_wind_kmh / 3.6

st.sidebar.subheader("التحكم في عدم تطابق السلاسل (String Mismatch)")
# محاكاة تأثير ترسب موضعي على محول معين
affected_inverter = st.sidebar.selectbox("المحول المتأثر بخلل موضعي / غبار كثيف", [f"Inverter Block {i+1}" for i in range(num_inverters)])
localized_soiling_extra = st.sidebar.slider("نسبة الغبار الإضافية على السلسلة المتأثرة (%)", min_value=0.0, max_value=40.0, value=15.0, step=5.0)

days_since_cleaning = st.sidebar.slider("الأيام العامة منذ آخر تنظيف", min_value=1, max_value=90, value=15, step=1)
max_soiling_limit = 40.0
base_soiling = max_soiling_limit * (1.0 - np.exp(-0.04 * days_since_cleaning))

albedo = st.sidebar.slider("معامل الانعكاس والأرضية (Albedo)", min_value=0.1, max_value=1.0, value=0.35, step=0.05)
tilt_error = st.sidebar.slider("خطأ زاوية الميل العام (Degrees °)", min_value=0.0, max_value=90.0, value=2.0, step=1.0)
tariff = st.sidebar.number_input("تعرفة الكهرباء المؤسسية (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")
cleaning_cost = st.sidebar.number_input("تكلفة عقد التنظيف الميداني (ر.ع)", min_value=10.0, max_value=500.0, value=75.0, step=5.0)

@st.cache_data
def run_distributed_simulation(total_cap, n_inv, affected_block, extra_soiling, base_soil, alb, tilt_err, t_amb, wind):
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
    
    results_dict = {}
    total_actual_power = np.zeros(len(times))
    total_ideal_power = np.zeros(len(times))
    
    for i in range(n_inv):
        block_name = f"Inverter Block {i+1}"
        base_power = (ghi / peak_ghi) * block_capacity * albedo_factor
        base_power = base_power.clip(lower=0)
        ideal_block_power = base_power * temp_factor
        
        # تطبيق فقدان الغبار الخاص بكل محول (سلاسل موزعة)
        block_soiling = base_soil + (extra_soiling if block_name == affected_block else 0.0)
        block_degradation = min(100.0, block_soiling + (tilt_err * 0.5))
        actual_factor = max(0.0, 1.0 - (block_degradation / 100.0))
        actual_block_power = ideal_block_power * actual_factor
        
        results_dict[f'{block_name} (الفعلي)'] = actual_block_power
        total_actual_power += actual_block_power
        total_ideal_power += ideal_block_power

    results_dict['إجمالي الإنتاج المثالي'] = total_ideal_power
    results_dict['إجمالي الإنتاج الفعلي للمحطة'] = total_actual_power
    
    return pd.DataFrame(results_dict, index=times)

df_results = run_distributed_simulation(total_capacity, num_inverters, affected_inverter, localized_soiling_extra, base_soiling, albedo, tilt_error, live_temp, live_wind)

ideal_total = df_results['إجمالي الإنتاج المثالي'].sum()
actual_total = df_results['إجمالي الإنتاج الفعلي للمحطة'].sum()
loss_kwh = ideal_total - actual_total
daily_financial_loss = loss_kwh * tariff
accumulated_loss = daily_financial_loss * days_since_cleaning

# حساب نافذة الصيانة التنبؤية
temp_accumulated = accumulated_loss
simulated_days_ahead = 0
while temp_accumulated < cleaning_cost and simulated_days_ahead < 60:
    simulated_days_ahead += 1
    future_loss_kwh = loss_kwh * (1.0 + (simulated_days_ahead * 0.02))
    temp_accumulated += future_loss_kwh * tariff

optimal_cleaning_window = days_since_cleaning + simulated_days_ahead

col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي الفقد اليومي للمحطة", f"{loss_kwh:.2f} kWh")
col2.metric("الخسارة المالية اليومية", f"{daily_financial_loss:.3f} ر.ع")
col3.metric("المحول الأكثر تأثراً", affected_inverter)
col4.metric("نافذة الصيانة المثلى", f"بعد {optimal_cleaning_window} يوماً")

st.markdown("---")

if accumulated_loss >= cleaning_cost:
    st.error(f"🚨 **إنذار تشغيلي حرج للطبولوجيا الموزعة:** الخسائر المتراكمة ({accumulated_loss:.2f} ر.ع) في **{affected_inverter}** والسلاسل المرتبطة به تجاوزت تكلفة الصيانة. **يُطلب تحريك فريق الصيانة الفورية لمعالجة الخلل الموضعي.**")
else:
    st.success(f"✅ **استقرار أداء المحولات:** توزيع الأحمال ضمن النطاق المقبول. المحول المحدّد ({affected_inverter}) يسجل ضغطاً إضافياً بسبب الترسبات الموضعية ولكن دون تجاوز عتبة الجدوى بعد.")

st.subheader("مقارنة أداء المحولات الموزعة (Inverter Blocks)")
# عرض منحنيات المحولات الفردية للإدارة الهندسية
inverter_columns = [f"Inverter Block {i+1} (الفعلي)" for i in range(num_inverters)]
st.line_chart(df_results[inverter_columns + ['إجمالي الإنتاج المثالي', 'إجمالي الإنتاج الفعلي للمحطة']])

st.subheader("🤖 تقرير تحليل البنية الموزعة والأداء المؤسسي (Interactions API & Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل الوكيل الذكي المؤسسي.")
else:
    if st.button("توليد تقرير تشخيص أداء المحولات والسلاسل الموزعة"):
        with st.spinner("الوكيل الذكي يحلل سلوك الكتل الكهربائية الفردية وأثر الاختلاف الموضعي للأتربة..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت مدير هندسة أصول الطاقة المتجددة وخبير تشغيل المحطات الكبرى.
                بيانات الطبولوجيا الموزعة للمحطة:
                - إجمالي القدرة: {total_capacity} kW موزعة على {num_inverters} محولات (Inverters).
                - المححول الذي يعاني من خلل موضعي أو ترسبات إضافية: {affected_inverter} بقيمة فقد إضافي {localized_soiling_extra}%.
                - الفقد الكلي اليومي للطاقة: {loss_kwh:.2f} kWh.
                - الخسارة المالية اليومية: {daily_financial_loss:.3f} ر.ع.
                - الخسارة المتراكمة: {accumulated_loss:.2f} ر.ع مقابل تكلفة صيانة {cleaning_cost} ر.ع.
                - ظروف مسقط الحية: حرارة {live_temp}°C، رياح {live_wind_kmh} km/h.
                
                قدم تقريراً تشغيلياً واحترافياً متعمقاً باللغة العربية للإدارة العليا في الشركة يتضمن:
                1. تقييم أداء المحولات الموزعة وكيف أثر الخلل في ({affected_inverter}) على كفاءة الـ MPPT الشاملة للمحطة.
                2. تحليل الجدوى الاقتصادية لصيانة السلسلة المتأثرة مقارنة بباقي الكتلة التشغيلية.
                3. التوصيات الهندسية الميدانية لإصلاح الخلل الموضعي وتجنب الاختلافات غير المتجانسة بين سلاسل الألواح.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير المؤسسي للطبولوجيا الموزعة بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
