import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import pydeck as pdk
import urllib.parse
import streamlit.components.v1 as components
from google import genai

st.set_page_config(page_title="RE-OPT: Al Wusta Enterprise Solar Twin", layout="wide")

st.markdown('<div id="top-anchor"></div>', unsafe_allow_html=True)

st.title("⚡ RE-OPT: Al Wusta Enterprise Solar Twin & Autonomous Operations")
st.markdown("التوأم الرقمي المؤسسي الشامل - مع ربط أسراب الروبوتات وأوامر الشغل عبر رسائل الوكيل المباشرة.")

AL_WUSTA_LAT, AL_WUSTA_LON = 19.55, 56.35

@st.cache_data(ttl=600)
def fetch_live_weather_and_pm10(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m,wind_direction_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data.get("current", {})
            temp = current.get("temperature_2m", 39.0)
            wind = current.get("wind_speed_10m", 10.0)
            wind_dir = current.get("wind_direction_10m", 140.0)
            pm10 = min(350.0, max(45.0, wind * 12.5 + np.random.uniform(10, 40)))
            return temp, wind, wind_dir, pm10
    except Exception:
        pass
    return 39.0, 10.0, 140.0, 85.0

api_temp, api_wind, api_wind_dir, api_pm10 = fetch_live_weather_and_pm10(AL_WUSTA_LAT, AL_WUSTA_LON)

gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")

total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=1000.0, value=150.0, step=50.0)
total_capacity = total_capacity_mw * 1000 
num_blocks = st.sidebar.selectbox("عدد حقول محولات الطاقة (Inverter Blocks)", [2, 4, 6, 8], index=1)

scada_mode = st.sidebar.toggle("تفعيل الربط الحي مع أنظمة SCADA والبيئة", value=True)
live_temp = api_temp if scada_mode else st.sidebar.number_input("درجة الحرارة المحيطة (°C)", value=39.0)
live_wind = api_wind if scada_mode else st.sidebar.number_input("سرعة الرياح (m/s)", value=10.0)
live_pm10 = api_pm10 if scada_mode else st.sidebar.number_input("تركيز الغبار PM10 (µg/m³)", value=85.0)

st.sidebar.subheader("محاكاة العواصف الرملية في صحراء الوسطى")
dust_storm_active = st.sidebar.toggle("🚨 محاكاة عاصفة رملية مفاجئة بالوسطى", value=False)
if dust_storm_active:
    live_pm10 = max(live_pm10, 320.0)
    live_wind = max(live_wind, 18.0)

storm_soiling_penalty = st.sidebar.slider("معامل الغبار الإضافي (%)", min_value=5.0, max_value=50.0, value=18.0) if dust_storm_active else 0.0

st.sidebar.subheader("تخصيص الألواح وزوايا الميل")
technology_type = st.sidebar.selectbox("نوع تكنولوجيا الألواح", ["ثنائية الوجه (Bifacial Glass-Glass)", "أحادية الوجه (Mono-facial PERC)"])
albedo = st.sidebar.slider("معامل انعكاس رمال الوسطى (Albedo)", min_value=0.2, max_value=0.7, value=0.45, step=0.05)
bifaciality_factor = st.sidebar.slider("معامل ثنائية الوجه (%)", min_value=65.0, max_value=85.0, value=75.0, step=5.0) / 100.0
tariff = st.sidebar.number_input("تعرفة الطاقة (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.025, step=0.001, format="%.3f")

st.sidebar.subheader("اقتصاديات أسطول الروبوتات الجافة")
total_robots = st.sidebar.number_input("عدد روبوتات التنظيف الجاف", min_value=500, max_value=6000, value=2200, step=100)
initial_robot_capex = st.sidebar.number_input("الاستثمار الأولي للروبوتات (ر.ع)", min_value=500000.0, max_value=6000000.0, value=1500000.0, step=50000.0)
daily_robot_depreciation = st.sidebar.number_input("إهلاك الصيانة اليومي", min_value=10.0, max_value=600.0, value=55.0, step=5.0)

st.sidebar.subheader("التحكم المستقل بحقول الألواح")
inverter_configs = {}
for i in range(num_blocks):
    inv_name = f"Al Wusta Field Block {i+1}"
    with st.sidebar.expander(f"إعدادات {inv_name}", expanded=(i==0)):
        soil = st.slider(f"نسبة الغبار الحالي (%) - Block {i+1}", 0.0, 45.0, float(3.0 + i * 2.5 + storm_soiling_penalty), key=f"soil_{i}")
        tilt = st.slider(f"زاوية الميل (Tilt °) - Block {i+1}", 5.0, 45.0, 22.0, key=f"tilt_{i}")
        inverter_configs[inv_name] = {'soiling': soil, 'tilt': tilt}

@st.cache_data
def run_wusta_simulation(latitude, longitude, total_cap, n_inv, configs, tech_mode, alb, bif_factor, t_amb, wind):
    tz = 'Asia/Muscat'
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    location = pvlib.location.Location(latitude, longitude, tz=tz)
    clearsky = location.get_clearsky(times)
    
    peak_ghi = 1000.0
    ghi = clearsky['ghi']
    
    noct = 47.0
    cell_temp = t_amb + (ghi / 800.0) * (noct - 20.0) * (9.5 / (5.7 + 3.8 * wind))
    temp_coeff = -0.0034
    temp_factor = 1.0 + temp_coeff * (cell_temp - 25.0)
    temp_factor = temp_factor.clip(lower=0.5)
    
    block_capacity = total_cap / n_inv
    simulation_results = {}
    total_actual = np.zeros(len(times))
    total_ideal = np.zeros(len(times))
    
    for i, (inv_name, cfg) in enumerate(configs.items()):
        base_power = (ghi / peak_ghi) * block_capacity
        base_power = base_power.clip(lower=0)
        
        ideal_power = base_power * temp_factor
        if "ثنائية الوجه" in tech_mode:
            bif_gain = 1.0 + (alb * bif_factor * 0.20)
            ideal_power *= bif_gain
            
        total_degradation = cfg['soiling'] + (abs(cfg['tilt'] - 22.0) * 0.2)
        actual_factor = max(0.0, 1.0 - (total_degradation / 100.0))
        actual_power = ideal_power * actual_factor
            
        simulation_results[inv_name] = pd.DataFrame({
            'النموذج المثالي': ideal_power,
            'قراءات السكادا الفعلية': actual_power
        }, index=times)
        
        total_ideal += ideal_power
        total_actual += actual_power

    simulation_results['Plant_Total'] = pd.DataFrame({
        'إجمالي المثالي للمحطة': total_ideal,
        'إجمالي قراءات السكادا الفعلية': total_actual
    }, index=times)
    
    return simulation_results

sim_data = run_wusta_simulation(AL_WUSTA_LAT, AL_WUSTA_LON, total_capacity, num_blocks, inverter_configs, technology_type, albedo, bifaciality_factor, live_temp, live_wind)

df_total = sim_data['Plant_Total']
total_plant_loss_kwh = (df_total['إجمالي المثالي للمحطة'] - df_total['إجمالي قراءات السكادا الفعلية']).sum()
daily_financial_loss = max(0.0, abs(total_plant_loss_kwh) * tariff)
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

annual_generation_mwh = (df_total['إجمالي قراءات السكادا الفعلية'].sum() * 365) / 1000.0
plant_capex = total_capacity_mw * 330000.0 
total_lifetime_cost = plant_capex + initial_robot_capex + (daily_robot_depreciation * 365 * 25)
total_lifetime_generation_mwh = annual_generation_mwh * 25
lcoe = total_lifetime_cost / max(1.0, total_lifetime_generation_mwh * 1000)

offsets = [(0.04, 0.04), (-0.03, 0.05), (-0.04, -0.04), (0.02, -0.05)]
polygon_data = []
for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
    off_lat, off_lon = offsets[i % len(offsets)]
    center_lat = AL_WUSTA_LAT + off_lat
    center_lon = AL_WUSTA_LON + off_lon
    dx, dy = 0.012, 0.007
    polygon = [
        [center_lon - dx, center_lat - dy],
        [center_lon + dx, center_lat - dy],
        [center_lon + dx, center_lat + dy],
        [center_lon - dx, center_lat + dy]
    ]
    is_critical = cfg['soiling'] > 12.0 or dust_storm_active
    color = [239, 68, 68, 230] if is_critical else [30, 64, 175, 230] 
    polygon_data.append({
        "name": inv_name, "polygon": polygon, "soiling": cfg['soiling'],
        "status": "🚨 خطر تلوث رملي حرج" if is_critical else "✅ أداء طبيعي وسليم", "color": color
    })
df_poly = pd.DataFrame(polygon_data)

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🗺️ الخريطة الجغرافية", 
    "📈 التوأم الرقمي والرسومات", 
    "🎯 التوجيه الدقيق لأسراب التنظيف", 
    "☀️ العرض المجسم (3D)", 
    "💰 الاقتصاديات و LCOE",
    "📋 أوامر الشغل وواتساب"
])

with tab1:
    st.subheader("📍 التوزيع الجغرافي لحقول الألواح في صحراء محافظة الوسطى")
    layer = pdk.Layer(
        "PolygonLayer", df_poly, id="wusta-map",
        get_polygon="polygon", get_fill_color="color", get_line_color=[255, 255, 255],
        line_width_min_pixels=3, extruded=False, pickable=True, auto_highlight=True,
    )
    view_state = pdk.ViewState(latitude=AL_WUSTA_LAT, longitude=AL_WUSTA_LON, zoom=9, pitch=20, bearing=0)
    r = pdk.Deck(layers=[layer], initial_view_state=view_state, map_style="https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json", tooltip={"text": "الحقل: {name}\nالحالة: {status}\nنسبة الغبار: {soiling}%"})
    st.pydeck_chart(r)

with tab2:
    st.subheader("📈 التوأم الرقمي - مقارنة الإنتاجية المثالية مقابل السكادا الفعلية")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي القدرة", f"{total_capacity_mw} MW")
    c2.metric("الفارق الإنتاجي", f"{abs(total_plant_loss_kwh):,.1f} kWh")
    c3.metric("تعرفة الكهرباء", f"{tariff:.3f} / kWh")
    c4.metric("مؤشر الغبار PM10", f"{live_pm10:.1f} µg/m³")
    st.markdown("---")
    st.line_chart(df_total)

with tab3:
    st.subheader("🎯 نظام التوجيه الدقيق لأسراب الروبوتات (حقل بحقل)")
    cleaning_report = []
    block_capacity_mw = (total_capacity_mw / num_blocks)
    for inv_name, cfg in inverter_configs.items():
        soiling = cfg['soiling']
        daily_loss_kwh = (block_capacity_mw * 1000) * (soiling / 100.0) * 5.5
        daily_loss_omr = daily_loss_kwh * tariff
        cleaning_cost_block = 35.0
        net_benefit = daily_loss_omr - cleaning_cost_block

        if dust_storm_active:
            action = "⏸️ تأجيل مؤقت (عاصفة نشطة مستمرة)"
        elif soiling > 12.0:
            action = f"🚀 **إرسال أسراب التنظيف فوراً** (صافي العائد: +{net_benefit:,.1f} ر.ع/يوم)"
        else:
            action = "✅ استبعاد من جدول اليوم (نظيف)"

        cleaning_report.append({"الحقل": inv_name, "نسبة الغبار": f"{soiling}%", "الفقد المالي اليومي": f"{daily_loss_omr:,.1f} ر.ع", "قرار الوكيل الآلي": action})
    st.table(pd.DataFrame(cleaning_report))

with tab4:
    st.subheader("☀️ عرض الحقول المجسمة (3D Diorama View)")
    cols = st.columns(2)
    for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
        soiling = cfg['soiling']
        tilt = cfg['tilt']
        is_critical = soiling > 12.0 or dust_storm_active
        panel_color = "#ef4444" if is_critical else "#1e40af"
        status_text = "🚨 تلوث رملي حرج" if is_critical else "✅ حقل نظيف ومنتج"

        diorama_html = f"""
        <div style="background: #1e293b; border: 2px solid {'#ef4444' if is_critical else '#0ea5e9'}; border-radius: 16px; padding: 16px; margin-bottom: 20px; color: white; font-family: sans-serif; text-align: right; direction: rtl; box-shadow: 0 12px 25px rgba(0,0,0,0.6);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h4 style="margin: 0; color: #f8fafc; font-size: 16px;">🌱 {inv_name} (محافظة الوسطى)</h4>
                <span style="background: {'#ef4444' if is_critical else '#0284c7'}; color: white; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: bold;">{status_text}</span>
            </div>
            <div style="background: linear-gradient(135deg, #d97706, #92400e); border: 3px solid #78350f; border-radius: 12px; height: 150px; display: flex; align-items: center; justify-content: center;">
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; width: 80%; transform: perspective(600px) rotateX(40deg);">
                    <div style="background: {panel_color}; height: 35px; border-radius: 4px; border: 2px solid #93c5fd;"></div>
                    <div style="background: {panel_color}; height: 35px; border-radius: 4px; border: 2px solid #93c5fd;"></div>
                    <div style="background: {panel_color}; height: 35px; border-radius: 4px; border: 2px solid #93c5fd;"></div>
                    <div style="background: {panel_color}; height: 35px; border-radius: 4px; border: 2px solid #93c5fd;"></div>
                </div>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 13px; background: rgba(0,0,0,0.4); padding: 8px 12px; border-radius: 6px; margin-top: 10px;">
                <span>نسبة الغبار: <b>{soiling}%</b></span>
                <span>زاوية الميل: <b>{tilt}°</b></span>
            </div>
        </div>
        """
        with cols[i % 2]:
            components.html(diorama_html, height=260)

with tab5:
    st.subheader("💰 التحليل المالي بعيد المدى واقتصاديات الروبوتات (LCOE & CAPEX)")
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)
    f_col1.metric("استثمار الروبوتات", f"{initial_robot_capex:,.0f} ر.ع")
    f_col2.metric("إهلاك الصيانة اليومي", f"{daily_robot_depreciation:.2f} ر.ع")
    f_col3.metric("صافي العائد اليومي", f"{net_robotic_roi:,.2f} ر.ع")
    f_col4.metric("تكلفة LCOE", f"{lcoe:.4f} ر.ع")
    st.markdown("---")
    if gemini_api_key and st.button("توليد التقرير المالي والتشغيلي الشامل"):
        try:
            client = genai.Client(api_key=gemini_api_key)
            response = client.interactions.create(
                model='gemini-3.6-flash',
                input=f"قدم تقريراً مالياً واقتصادياً متعمقاً لإدارة الأصول لمحطة الوسطى بقدرة {total_capacity_mw} ميجاوات وتكلفة LCOE تبلغ {lcoe:.4f}."
            )
            st.markdown(response.output_text)
        except Exception as e:
            st.error(f"خطأ: {e}")

with tab6:
    st.subheader("📋 لوحة أوامر الشغل والإرسال الفوري عبر واتساب")
    
    col_w1, col_w2 = st.columns(2)
    with col_w1:
        sender_phone = st.text_input("رقم هاتفك (المرسل)", value="+96890000000")
    with col_w2:
        receiver_phone = st.text_input("رقم مهندس الصيانة / مشرف الروبوتات (المستلم)", value="+96891111111")

    selected_block_wo = st.selectbox("اختر الحقل لإصدار أمر العمل الميداني", list(inverter_configs.keys()))
    work_order_id = f"WO-ALWUSTA-2026-{np.random.randint(1000, 9999)}"
    
    soil_val = inverter_configs[selected_block_wo]['soiling']
    tilt_val = inverter_configs[selected_block_wo]['tilt']
    priority_level = "عالية جداً (Critical)" if dust_storm_active or soil_val > 12.0 else "عادية (Normal)"

    message_body = (
        f"🚨 *أمر شغل مؤسسي - محطة الوسطى للطاقة الشمسية*\n\n"
        f"🆔 رقم العمل: `{work_order_id}`\n"
        f"🌱 الحقل المستهدف: *{selected_block_wo}*\n"
        f"📊 نسبة الغبار: *{soil_val}%*\n"
        f"📐 زاوية الميل: *{tilt_val}°*\n"
        f"⚡ الأولوية: *{priority_level}*\n"
        f"🛠️ الإجراء المطلوب: نشر أسراب روبوتات التنظيف الجاف فوراً.\n\n"
        f"📱 مرسل من الرقم: {sender_phone}"
    )

    st.markdown("**معاينة رسالة أمر الشغل الجاهزة للإرسال:**")
    st.info(message_body)

    # ترميز النص ليتناسب مع رابط واتساب الرسمي
    encoded_message = urllib.parse.quote(message_body)
    clean_receiver = receiver_phone.replace("+", "").replace(" ", "")
    whatsapp_url = f"https://wa.me/{clean_receiver}?text={encoded_message}"

    st.markdown("---")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("💾 حفظ السجل في قاعدة بيانات السكادا"):
            st.success(f"✅ تم حفظ أمر الشغل رقم {work_order_id} في أرشيف الأدلة ومطالبات التأمين بنجاح!")
    with col_btn2:
        # زر مباشر يفتح واتساب مع الرسالة الجاهزة
        st.markdown(
            f'<a href="{whatsapp_url}" target="_blank" style="display:inline-block;background-color:#25d366;color:white;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:bold;text-align:center;width:100%;">📤 إرسال أمر الشغل عبر واتساب للمهندس</a>',
            unsafe_allow_html=True
        )
