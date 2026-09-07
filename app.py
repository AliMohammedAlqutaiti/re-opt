import streamlit as st
import pandas as pd
import numpy as np
import pvlib
from google import genai

st.set_page_config(page_title="RE-OPT: Digital Twin Dashboard", layout="wide")

st.title("⚡ RE-OPT: AI-Powered Renewable Energy Digital Twin")
st.markdown("منصة التوأم الرقمي والوكيل الذكي المعتمد على Google Gemini لمراقبة وتشخيص أصول الطاقة الشمسية.")

st.sidebar.header("إعدادات المحاكاة والذكاء الاصطناعي")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
rated_capacity = st.sidebar.slider("قدرة المحطة (kW)", min_value=5.0, max_value=50.0, value=10.0, step=5.0)
soiling_loss = st.sidebar.slider("نسبة فقدان الغبار (Soiling %)", min_value=0.0, max_value=30.0, value=15.0, step=1.0)
tariff = st.sidebar.number_input("تعرفة الكهرباء (ر.ع / kWh)", value=0.030, step=0.005)

@st.cache_data
def run_simulation(capacity, soiling_pct):
    site_latitude = 23.58
    site_longitude = 58.38
    tz = 'Asia/Muscat'

    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    clearsky = pvlib.location.Location(site_latitude, site_longitude, tz).get_clearsky(times)

    peak_ghi = 1000.0
    expected_power = (clearsky['ghi'] / peak_ghi) * capacity
    expected_power = expected_power.clip(lower=0)

    actual_factor = 1.0 - (soiling_pct / 100.0)
    actual_power = expected_power * actual_factor

    results = pd.DataFrame({
        'التوأم الرقمي (المثالي)': expected_power,
        'الواقع (الحساسات الفعلية)': actual_power
    }, index=times)

    return results

df_results = run_simulation(rated_capacity, soiling_loss)

loss_kwh = (df_results['التوأم الرقمي (المثالي)'] - df_results['الواقع (الحساسات الفعلية)']).sum()
financial_loss = loss_kwh * tariff

col1, col2, col3 = st.columns(3)
col1.metric("إجمالي الطاقة المفقودة اليوم", f"{loss_kwh:.2f} kWh")
col2.metric("الخسارة المالية التقديرية", f"{financial_loss:.3f} ر.ع")
col3.metric("مستوى كفاءة الألواح الحالي", f"{100 - soiling_loss}%")

st.markdown("---")

st.subheader("مقارنة الأداء: التوأم الرقمي مقابل الإنتاج الواقعي")
st.line_chart(df_results)

st.subheader("🤖 تقرير تحليل الوكيل الذكي (Gemini AI Agent Report)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل تقرير الوكيل الذكي.")
else:
    if st.button("توليد التقرير التحليلي باستخدام Gemini"):
        with st.spinner("الوكيل الذكي يقوم بتحليل البيانات وإعداد التقرير..."):
            try:
                client = genai.Client(api_key=gemini_api_key)

                prompt = f"""
                أنت وكيل ذكاء اصطناعي خبير في تشخيص محطات الطاقة الشمسية.
                بيانات المحطة الحالية:
                - قدرة المحطة: {rated_capacity} kW
                - نسبة فقدان الكفاءة بسبب الغبار: {soiling_loss}%
                - الطاقة المفقودة اليوم: {loss_kwh:.2f} kWh
                - الخسارة المالية اليومية: {financial_loss:.3f} ريال عماني
                - تعرفة الكهرباء: {tariff} ر.ع/kWh

                قدم تقريراً تشغيلياً واحترافياً باللغة العربية يتضمن:
                1. تحليل السبب الجذري.
                2. التقييم المالي والأثر الاقتصادي.
                3. توصية تنفيذية واضحة لصناع القرار متى يتم جدولة التنظيف.
                """

                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )

                st.success("تم توليد التقرير بنجاح!")
                st.markdown(response.text)

            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
