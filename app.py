import streamlit as st
import pandas as pd
import numpy as np
import pvlib
from google import genai

st.set_page_config(page_title="RE-OPT: Digital Twin Dashboard", layout="wide")

st.title("⚡ RE-OPT: AI-Powered Renewable Energy Digital Twin")
st.markdown("منصة التوأم الرقمي والوكيل الذكي المعتمد على Google Gemini لمراقبة وتشخيص أصول الطاقة الشمسية.")

st.sidebar.header("إعدادات المحاكاة والتشخيص")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
rated_capacity = st.sidebar.slider("قدرة المحطة (kW)", min_value=5.0, max_value=50.0, value=10.0, step=5.0)

st.sidebar.subheader("عوامل فقدان الأداء والمشاكل التشغيلية")
soiling_loss = st.sidebar.slider("نسبة فقدان الغبار (Soiling %)", min_value=0.0, max_value=30.0, value=15.0, step=1.0)
albedo = st.sidebar.slider("معامل الانعكاس والأرضية (Albedo)", min_value=0.1, max_value=0.8, value=0.2, step=0.05)
tilt_error = st.sidebar.slider("خطأ زاوية الميل (Degrees °)", min_value=0.0, max_value=20.0, value=5.0, step=1.0)
temp_loss = st.sidebar.slider("فقدان الحرارة الزائدة (%)", min_value=0.0, max_value=15.0, value=4.0, step=0.5)

# تم تحديث هذا السطر لضمان سلاسة وسهولة تعديل التعرفة
tariff = st.sidebar.number_input("تعرفة الكهرباء (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

@st.cache_data
def run_simulation(capacity, soiling_pct, alb, tilt_err, t_loss):
    site_latitude = 23.58
    site_longitude = 58.38
    tz = 'Asia/Muscat'
    
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    clearsky = pvlib.location.Location(site_latitude, site_longitude, tz).get_clearsky(times)
    
    peak_ghi = 1000.0
    base_power = (clearsky['ghi'] / peak_ghi) * capacity
    base_power = base_power.clip(lower=0)
    
    albedo_factor = 1.0 + ((alb - 0.2) * 0.1) 
    expected_power = base_power * albedo_factor
    
    total_degradation_pct = soiling_pct + (tilt_err * 0.8) + t_loss
    actual_factor = max(0.0, 1.0 - (total_degradation_pct / 100.0))
    actual_power = expected_power * actual_factor
    
    results = pd.DataFrame({
        'التوأم الرقمي (المثالي مع Albedo)': expected_power,
        'الواقع (بعد المشاكل التشغيلية)': actual_power
    }, index=times)
    
    return results

df_results = run_simulation(rated_capacity, soiling_loss, albedo, tilt_error, temp_loss)

loss_kwh = (df_results['التوأم الرقمي (المثالي مع Albedo)'] - df_results['الواقع (بعد المشاكل التشغيلية)']).sum()
financial_loss = loss_kwh * tariff

col1, col2, col3 = st.columns(3)
col1.metric("إجمالي الطاقة المفقودة اليوم", f"{loss_kwh:.2f} kWh")
col2.metric("الخسارة المالية التقديرية", f"{financial_loss:.3f} ر.ع")
col3.metric("مستوى كفاءة الألواح الفعلي", f"{max(0, 100 - (soiling_loss + tilt_error*0.8 + temp_loss)):.1f}%")

st.markdown("---")

st.subheader("مقارنة الأداء: التوأم الرقمي (مع تأثير Albedo) مقابل الواقع التشغيلي")
st.line_chart(df_results)

st.subheader("🤖 تقرير تحليل الوكيل الذكي الشامل (Interactions API & Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل تقرير الوكيل الذكي.")
else:
    if st.button("توليد التقرير التحليلي المتكامل"):
        with st.spinner("الوكيل الذكي يعالج بيانات الأداء والتشخيص عبر Interactions API..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت وكيل ذكاء اصطناعي خبير في هندسة وتخطيط محطات الطاقة الشمسية.
                بيانات التشخيص الحالية للمحطة:
                - قدرة المحطة: {rated_capacity} kW
                - فقدان الغبار (Soiling): {soiling_loss}%
                - معامل انعكاس الأرضية (Albedo): {albedo}
                - خطأ زاوية الميل (Tilt Error): {tilt_error}°
                - فقدان الحرارة الزائدة (Thermal Losses): {temp_loss}%
                - الطاقة المفقودة الإجمالية اليوم: {loss_kwh:.2f} kWh
                - الخسارة المالية اليومية: {financial_loss:.3f} ريال عماني
                - تعرفة الكهرباء: {tariff} ر.ع/kWh
                
                قدم تقريراً تشغيلياً واحترافياً متعمقاً باللغة العربية يتضمن:
                1. تحليل الأسباب الجذرية (تأثير الغبار، انحراف زاوية الميل، الانعكاس والوضاءة Albedo، والحرارة).
                2. التقييم المالي والأثر الاقتصادي لهذه المشاكل مجتمعة بالاعتماد على التعرفة المدخلة.
                3. توصيات هندسية تنفيذية واضحة لصناع القرار لجدولة الصيانة ومعالجة زاوية الميل وتنظيف الألواح.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير الهندسي الشامل بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
