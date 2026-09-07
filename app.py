import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
from google import genai

st.set_page_config(page_title="RE-OPT: Enterprise Robotic Digital Twin", layout="wide")

st.title("⚡ RE-OPT: Enterprise Digital Twin - Manah Robotic Solar Fleet")
st.markdown("منصة التوأم الرقمي المؤسسي - نموذج أسطول الروبوتات الجافة (Dry-Cleaning Robots) على غرار مشاريع منح الكبرى.")

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

st.sidebar.header("إعدادات أسطول الروبوتات والبنية المؤسسية")
gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=500.0, value=100.0, step=50.0) * 1000 # تحويل إلى kW
num_inverters = st.sidebar.selectbox("عدد محولات الطاقة (Inverter Blocks)", [2, 4, 6], index=1)

st.sidebar.subheader("بيانات الموقع الحي (سلطنة عمان)")
live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=55.0, value=float(api_temp), step=0.5)
live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind), step=0.5)
live_wind = live_wind_kmh / 3.6

albedo = st.sidebar.slider("معامل الانعكاس والأرضية (Albedo)", min_value=0.1, max_value=1.0, value=0.35, step=0.05)
tariff = st.sidebar.number_input("تعرفة الكهرباء المؤسسية (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.030, step=0.001, format="%.3f")

# اقتصاديات أسطول الروبوتات الجافة (بدون مياه أو عمالة يدوية)
st.sidebar.subheader("اقتصاديات أسطول الروبوتات الآلية (Robotic O&M)")
robot_fleet_size = st.sidebar.number_input("عدد روبوتات التنظيف الجاف النشطة", min_value=100, max_value=5000, value=1200, step=100)
daily_robot_depreciation = st.sidebar.number_input("التكلفة اليومية لإهلاك وصيانة الروبوتات (ر.ع / يوم)", min_value=5.0, max_value=200.0, value=35.0, step=5.0)
days_since_robot_sweep = st.sidebar.slider("دورات التشغيل الجاف بدون مسح كامل (أيام)", min_value=1, max_value=30, value=5, step=1)

st.sidebar.subheader("التحكم المستقل لكتل المحولات (Inverter Blocks)")
inverter_configs = {}
for i in range(num_inverters):
    inv_name = f"Inverter Block {i+1}"
    with st.sidebar.expander(f"إعدادات تشغيل {inv_name}", expanded=(i==0)):
        inv_soiling = st.slider(f"تراكم الغبار (%) - {inv_name}", min_value=0.0, max_value=20.0, value=float(3.0 + (i * 1.5)), step=0.5, key=f"soiling_inv_{i}")
        inv_tilt_err = st.slider(f"خطأ الميل (°) - {inv_name}", min_value=0.0, max_value=10.0, value=float(i * 1.0), step=0.5, key=f"tilt_inv_{i}")
        inverter_configs[inv_name] = {'soiling': inv_soiling, 'tilt_error': inv_tilt_err}

@st.cache_data
def run_robotic_simulation(total_cap, n_inv, configs, alb, t_amb, wind):
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
            'الواقع التشغيلي (أسطول الروبوتات)': actual_power
        }, index=times)
        
    return simulation_results

inverter_data = run_robotic_simulation(total_capacity, num_inverters, inverter_configs, albedo, live_temp, live_wind)

total_plant_loss_kwh = 0
for inv_name, df_block in inverter_data.items():
    block_loss = (df_block['التوأم الرقمي (المرجع المثالي)'] - df_block['الواقع التشغيلي (الواقع التشغيلي (أسطول الروبوتات))'] if 'الواقع التشغيلي (أسطول الروبوتات)' in df_block.columns else df_block.iloc[:, 1]).sum()
    total_plant_loss_kwh += block_loss

daily_financial_loss = total_plant_loss_kwh * tariff
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

st.subheader("🤖 مؤشرات كفاءة أسطول الروبوتات الجافة (Manah-Scale Model)")
col_r1, col_r2, col_r3, col_r4 = st.columns(4)
col_r1.metric("تكلفة المياه والعمالة اليدوية", "0.00 ر.ع (100% روبوتات)")
col_r2.metric("إجمالي الفقد اليومي للطاقة", f"{total_plant_loss_kwh:,.1f} kWh")
col_r3.metric("تكلفة إهلاك الروبوتات اليومية", f"{daily_robot_depreciation:.2f} ر.ع")
col_r4.metric("صافي العائد الاقتصادي اليومي", f"{net_robotic_roi:,.2f} ر.ع")

if net_robotic_roi > 0:
    st.success(f"✅ **الجدوى التشغيلية ممتازة:** قيمة الطاقة المفملة التي تم توفيرها عبر الروبوتات تتجاوز تكلفة إهلاك الأسطول بقيمة صافية **{net_robotic_roi:,.2f} ر.ع يومياً**. النظام يعمل بأعلى كفاءة استثمارية.")
else:
    st.warning(f"⚠️ **تنبيه إهلاك الأسطول:** تكلفة تشغيل الروبوتات تفوق الخسارة الحالية. يُوصى بجدولة دورات مسح أطول للروبوتات لتقليل الاستهلاك.")

st.markdown("---")
st.subheader("📊 أداء الكتل الكهربائية مع تشغيل الأسطول الروبوتي")

for inv_name, df_block in inverter_data.items():
    st.markdown(f"**🔹 تحليلات أداء {inv_name}**")
    
    actual_col = [c for c in df_block.columns if 'الواقع التشغيلي' in c][0]
    ideal_sum = df_block['التوأم الرقمي (المرجع المثالي)'].sum()
    actual_sum = df_block[actual_col].sum()
    block_loss = ideal_sum - actual_sum
    block_financial_loss = block_loss * tariff
    
    col1, col2, col3 = st.columns(3)
    col1.metric(f"فقد الطاقة ({inv_name})", f"{block_loss:,.1f} kWh")
    col2.metric(f"الخسارة المالية ({inv_name})", f"{block_financial_loss:,.3f} ر.ع")
    col3.metric(f"إعدادات الروبوت/الغبار", f"تراكم: {inverter_configs[inv_name]['soiling']}%")
    
    st.line_chart(df_block)
    st.markdown("---")

st.subheader("🤖 تقرير تحليل الأداء المؤسسي لأسطول الروبوتات (Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل الوكيل الذكي.")
else:
    if st.button("توليد تقرير أداء أسطول الروبوتات المؤسسي"):
        with st.spinner("الوكيل الذكي يحلل اقتصاديات الروبوتات الجافة وكفاءة الإنتاج..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                
                prompt = f"""
                أنت مدير هندسة الأصول التشغيلية وخبير استراتيجي للطاقة الشمسية في المشاريع الكبرى (مثل محطات منح في عمان).
                بيانات المحطة والأسطول الروبوتي:
                - القدرة الإجمالية: {total_capacity/1000} MW موزعة على {num_inverters} كتل.
                - أسطول الروبوتات الجافة: {robot_fleet_size} روبوت نشط بدون استهلاك مياه أو عمالة يدوية ($0 تنظيف يدوي).
                - تكلفة إهلاك الروبوتات اليومية: {daily_robot_depreciation} ر.ع.
                - إجمالي الفقد اليومي للطاقة: {total_plant_loss_kwh:,.1f} kWh.
                - صافي العائد الاقتصادي اليومي بعد خصم إهلاك الروبوتات: {net_robotic_roi:,.2f} ر.ع.
                - ظروف مسقط الحية: حرارة {live_temp}°C، رياح {live_wind_kmh} km/h.
                
                قدم تقريراً تشغيلياً واحترافياً متعمقاً باللغة العربية للإدارة العليا يتضمن:
                1. تقييم كفاءة أسطول الروبوتات الجافة في حماية استثمارات المגהجا في البيئة الصحراوية القاسية.
                2. تحليل الجدوى المالية ومقارنة نموذج التشغيل الآلي التلقائي بالكامل مقابل التكاليف الباهظة للغسيل اليدوي والمائي.
                3. توصيات تشغيلية لإطالة عمر الروبوتات وتحسين دورات المسح.
                """
                
                interaction = client.interactions.create(
                    model='gemini-3.6-flash',
                    input=prompt
                )
                
                st.success("تم توليد التقرير المؤسسي لأسطول الروبوتات بنجاح!")
                st.markdown(interaction.output_text)
                
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
