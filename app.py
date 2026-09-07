import streamlit as st
import pandas as pd
import numpy as np
import pvlib

st.set_page_config(page_title="RE-OPT: Digital Twin Dashboard", layout="wide")

st.title("⚡ RE-OPT: AI-Powered Renewable Energy Digital Twin")
st.markdown("منصة التوأم الرقمي والوكيل الذكي لمراقبة وتشخيص أصول الطاقة الشمسية في سلطنة عمان.")

st.sidebar.header("إعدادات المحاكاة")
rated_capacity = st.sidebar.slider("قدرة المحطة (kW)", min_value=5.0, max_value=50.0, value=10.0, step=5.0)
soiling_loss = st.sidebar.slider("نسبة فقدان الغبار (Soiling %)", min_value=0.0, max_value=30.0, value=15.0, step=1.0)
tariff = st.sidebar.number_input("تعرفة الكهرباء (ر.ع / kWh)", value=0.030, step=0.005)

@st.cache_data
def run_simulation(capacity, soiling_pct):
    site_latitude = 23.58
    site_longitude = 58.38
    tz = 'Asia/Muscat'

    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='H', tz=tz)
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

st.subheader("تقرير تشخيص الوكيل الذكي (AI Agent Diagnosis)")

if soiling_loss > 10:
    st.error(f"⚠️ **تنبيه تشغيلي:** تم رصد انخفاض ملحوظ في الكفاءة بنسبة {soiling_loss}% مقارنة بنموذج التوأم الرقمي.")
    st.info(f"💡 **التوصية المقترحة:** بناءً على معدل تراكم الغبار في أجواء مسقط والخسارة المالية اليومية البالغة **{financial_loss:.3f} ر.ع**، يُنصح بجدولة فريق التنظيف للأقسام المتأثرة خلال 48 ساعة القادمة لضمان استرداد التكلفة.")
else:
    st.success("✅ **الحالة مستقرة:** أداء المحطة يتطابق بشكل جيد مع محاكاة التوأم الرقمي ولا توجد خسائر غير مبررة.")
