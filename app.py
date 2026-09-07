import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import pydeck as pdk
from google import genai

st.set_page_config(page_title="RE-OPT: Marmoul 3D Solar Twin", layout="wide")

st.title("⚡ RE-OPT: Marmoul Desert Solar Farm (Al Wusta) - 3D Digital Twin")
st.markdown("التوأم الرقمي المؤسسي - محطة طاقة شمسية بقطاع مرمول (محافظة الوسطى) مع عرض مرئي ثلاثي الأبعاد لصفوف الألواح وزوايا الميل وتراكم الغبار.")

@st.cache_data(ttl=600)
def fetch_live_weather(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data.get("current", {})
            temp = current.get("temperature_2m", 35.0)
            wind = current.get("wind_speed_10m", 7.5)
            return temp, wind
    except Exception:
        pass
    return 35.0, 7.5

# تعيين الموقع الجغرافي الافتراضي لمرمول، محافظة الوسطى (عمان)
st.sidebar.header("إعدادات الموقع الجغرافي (مرمول، الوسطى)")
lat, lon = 18.15, 55.18  # إحداثيات مرمول التقريبية في الوسطى
site_name = "Marmoul Solar Park, Al Wusta (Oman)"

api_temp, api_wind = fetch_live_weather(lat, lon)

gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=1000.0, value=150.0, step=50.0)
total_capacity = total_capacity_mw * 1000 
num_strings = st.sidebar.selectbox("عدد صفوف وأسراب الألواح الرئيسية (Solar Strings)", [4, 8, 12, 16], index=1)

scada_mode = st.sidebar.toggle("تفعيل الربط الحي مع أنظمة SCADA الصحراوية", value=True)

if scada_mode:
    live_temp = api_temp
    live_wind = api_wind
else:
    live_temp = st.sidebar.number_input("درجة الحرارة المحيطة (°C)", min_value=10.0, max_value=60.0, value=float(api_temp), step=0.5)
    live_wind_kmh = st.sidebar.number_input("سرعة الرياح (km/h)", min_value=0.0, max_value=100.0, value=float(api_wind*3.6), step=0.5)
    live_wind = live_wind_kmh / 3.6

st.sidebar.subheader("محاكاة العواصف الرملية في صحراء الوسطى")
dust_storm_active = st.sidebar.toggle("🚨 محاكاة عاصفة رملية في مرمول", value=False)
storm_soiling_penalty = st.sidebar.slider("معامل الفقد الإضافي للرمال (%)", min_value=5.0, max_value=50.0, value=20.0, step=2.5) if dust_storm_active else 0.0

st.sidebar.subheader("تخصيص الألواح وزوايا الميل (PV Module Specs)")
technology_type = st.sidebar.selectbox("نوع تكنولوجيا الألواح", ["ثنائية الوجه (Bifacial Glass-Glass)", "أحادية الوجه (Mono-facial PERC)", "هجين صحراوي"])
panel_wattage = st.sidebar.selectbox("قدرة اللوحة الواحدة (Watt)", [550, 600, 650, 700], index=2)
albedo = st.sidebar.slider("معامل انعكاس رمال الوسطى (Albedo)", min_value=0.2, max_value=0.7, value=0.45, step=0.05)
bifaciality_factor = st.sidebar.slider("معامل ثنائية الوجه (%)", min_value=65.0, max_value=85.0, value=75.0, step=5.0) / 100.0

tariff = st.sidebar.number_input("تعرفة الطاقة (ر.ع / kWh)", min_value=0.001, max_value=0.100, value=0.025, step=0.001, format="%.3f")

st.sidebar.subheader("اقتصاديات أسطول الروبوتات الجافة")
total_robots = st.sidebar.number_input("عدد روبوتات التنظيف الجاف", min_value=500, max_value=6000, value=2200, step=100)
initial_robot_capex = st.sidebar.number_input("الاستثمار الأولي للروبوتات (ر.ع)", min_value=500000.0, max_value=6000000.0, value=1500000.0, step=50000.0)
daily_robot_depreciation = st.sidebar.number_input("إهلاك الصيانة اليومي", min_value=10.0, max_value=600.0, value=55.0, step=5.0)

st.sidebar.subheader("التحكم المستقل لصفوف الألواح (String/Table Control)")
string_configs = {}
for i in range(num_strings):
    str_name = f"Solar Row {i+1}"
    with st.sidebar.expander(f"إعدادات صف الألواح {str_name}", expanded=(i==0)):
        base_s = 3.0 + (i * 1.2)
        str_soiling = st.slider(f"نسبة الغبار والرمال (%) - {str_name}", min_value=0.0, max_value=45.0, value=float(base_s + storm_soiling_penalty), step=0.5, key=f"soil_{i}")
        str_tilt = st.slider(f"زاوية ميل الصف (Tilt °) - {str_name}", min_value=5.0, max_value=45.0, value=float(22.0), step=1.0, key=f"tilt_{i}")
        string_configs[str_name] = {'soiling': str_soiling, 'tilt': str_tilt}

@st.cache_data
def run_marmoul_simulation(latitude, longitude, total_cap, n_str, configs, tech_mode, alb, bif_factor, t_amb, wind, is_live):
    tz = 'Asia/Muscat'
    times = pd.date_range('2026-06-01 06:00:00', '2026-06-01 18:00:00', freq='h', tz=tz)
    location = pvlib.location.Location(latitude, longitude, tz=tz)
    clearsky = location.get_clearsky(times)
    
    peak_ghi = 1000.0
    ghi = clearsky['ghi']
    
    noct = 47.0 # حرارة أعلى مناسبة لصحراء الوسطى
    cell_temp = t_amb + (ghi / 800.0) * (noct - 20.0) * (9.5 / (5.7 + 3.8 * wind))
    temp_coeff = -0.0034
    temp_factor = 1.0 + temp_coeff * (cell_temp - 25.0)
    temp_factor = temp_factor.clip(lower=0.5)
    
    string_capacity = total_cap / n_str
    
    simulation_results = {}
    total_mono_actual = np.zeros(len(times))
    total_bif_actual = np.zeros(len(times))
    total_actual = np.zeros(len(times))
    
    for i in range(n_str):
        str_name = f"Solar Row {i+1}"
        cfg = configs[str_name]
        
        base_power = (ghi / peak_ghi) * string_capacity
        base_power = base_power.clip(lower=0)
        
        mono_base = base_power * temp_factor
        bif_gain = 1.0 + (alb * bif_factor * 0.20)
        bif_base = base_power * temp_factor * bif_gain
        
        tilt_penalty = abs(cfg['tilt'] - 22.0) * 0.2
        total_degradation = cfg['soiling'] + tilt_penalty
        actual_factor = max(0.0, 1.0 - (total_degradation / 100.0))
        
        mono_actual_power = mono_base * actual_factor
        bif_actual_power = bif_base * actual_factor
        
        selected_actual = bif_actual_power if "ثنائية الوجه" in tech_mode or (tech_mode == "هجين صحراوي" and i % 2 == 0) else mono_actual_power
        
        if is_live:
            np.random.seed(200 + i)
            noise = np.random.normal(1.0, 0.007, len(times))
            scada_power = selected_actual * noise
        else:
            scada_power = selected_actual
            
        simulation_results[str_name] = pd.DataFrame({
            'الواقع (أحادى الوجه)': mono_actual_power,
            'الواقع (ثنائي الوجه)': bif_actual_power,
            'قراءات السكادا (IoT)': scada_power
        }, index=times)
        
        total_mono_actual += mono_actual_power
        total_bif_actual += bif_actual_power
        total_actual += scada_power

    simulation_results['Plant_Total'] = pd.DataFrame({
        'إجمالي الواقع (أحادى الوجه)': total_mono_actual,
        'إجمالي الواقع (ثنائي الوجه)': total_bif_actual,
        'إجمالي قراءات السكادا': total_actual
    }, index=times)
    
    return simulation_results

sim_data = run_marmoul_simulation(lat, lon, total_capacity, num_strings, string_configs, technology_type, albedo, bifaciality_factor, live_temp, live_wind, scada_mode)

df_total = sim_data['Plant_Total']
total_plant_loss_kwh = (df_total['إجمالي الواقع (ثنائي الوجه)'] - df_total['إجمالي قراءات السكادا']).sum()
daily_financial_loss = max(0.0, abs(total_plant_loss_kwh) * tariff)
net_robotic_roi = daily_financial_loss - daily_robot_depreciation

annual_generation_mwh = (df_total['إجمالي قراءات السكادا'].sum() * 365) / 1000.0
plant_capex = total_capacity_mw * 330000.0 
total_lifetime_cost = plant_capex + initial_robot_capex + (daily_robot_depreciation * 365 * 25)
total_lifetime_generation_mwh = annual_generation_mwh * 25
lcoe = total_lifetime_cost / max(1.0, total_lifetime_generation_mwh * 1000)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 التوأم الرقمي لمرمول", 
    "🔍 التشخيص الذكي للأعطال", 
    "🗺️ الخريطة التفاعلية ثلاثية الأبعاد لألواح مرمول", 
    "💰 الاقتصاديات و LCOE", 
    "📋 أوامر الشغل الآلية"
])

with tab1:
    if dust_storm_active:
        st.error("🚨 **تحذير طارئ في مرمول:** عاصفة رملية قوية تهب حالياً عبر سهل الوسطى. تم تفعيل استجابة الروبوتات الطارئة.")
    
    st.subheader(f"📈 الإنتاجية الفعلية لمحطة مرمول الصحراوية ({technology_type})")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي القدرة", f"{total_capacity_mw} MW")
    c2.metric("الفارق الإنتاجي", f"{abs(total_plant_loss_kwh):,.1f} kWh")
    c3.metric("قدرة اللوحة الواحدة", f"{panel_wattage} W")
    c4.metric("حالة الرمال", "عاصفة نشطة" if dust_storm_active else "مستقرة آلياً")

    st.markdown("---")
    st.line_chart(df_total)

with tab2:
    st.subheader("🔍 خوارزميات التشخيص والكشف عن الأعطال (FDD) لصفوف مرمول")
    fdd_summary = []
    for str_name, df_block in sim_data.items():
        if str_name == 'Plant_Total':
            continue
        
        ideal_sum = df_block['الواقع (ثنائي الوجه)'].sum()
        actual_sum = df_block['قراءات السكادا (IoT)'].sum()
        deviation_pct = ((ideal_sum - actual_sum) / ideal_sum) * 100 if ideal_sum > 0 else 0
        
        if deviation_pct > 10.0 or dust_storm_active:
            status = "🚨 تنبيه حرج: تراكم كثيف للرمال الصحراوية"
        elif deviation_pct > 4.0:
            status = "⚠️ تنبيه متوسط: انحراف طفيف في الصف"
        else:
            status = "✅ أداء الصف طبيعي وممتاز"
            
        fdd_summary.append({
            'صف الألواح (Solar Row)': str_name,
            'نسبة الانحراف (%)': f"{deviation_pct:.2f}%",
            'حالة التشخيص': status
        })
        
        with st.expander(f"تفاصيل صف {str_name} (الانحراف: {deviation_pct:.2f}%)"):
            st.write(f"الحالة: {status}")
            st.line_chart(df_block)

    st.table(pd.DataFrame(fdd_summary))

with tab3:
    st.subheader("🗺️ الخريطة المرئية ثلاثية الأبعاد لمصفوفات الألواح في مرمول (3D Panel GIS)")
    st.markdown("عرض مرئي تفصيلي يتيح لك رؤية **صفوف الألواح الشمسية الفعلية** في صحراء الوسطى؛ حيث يرتفع كل صف ويتغير لونه بناءً على **نسبة ترسب الرمال (Soil %)** وزاوية ميل الألواح.")

    # تجهيز إحداثيات مرئية لصفوف الألواح في مرمول
    map_panels = []
    for i in range(num_strings):
        str_name = f"Row {i+1}"
        cfg = string_configs[f"Solar Row {i+1}"]
        
        # توزيع صفوف الألواح بشكل شبكي مرئي ثلاثي الأبعاد
        panel_lat = lat + (i * 0.003) - (num_strings * 0.001)
        panel_lon = lon + ((i % 2) * 0.008) - 0.004
        
        map_panels.append({
            'row_name': str_name,
            'lat': panel_lat,
            'lon': panel_lon,
            'soiling': cfg['soiling'],
            'tilt': cfg['tilt'],
            'wattage': panel_wattage,
            'elevation': float(cfg['soiling'] * 12.0 + 30.0), # ارتفاع مرئي يعكس كثافة الرمال على الألواح
            'color': [220, 50, 30, 220] if cfg['soiling'] > 18 else [20, 160, 220, 220]
        })
        
    df_panels = pd.DataFrame(map_panels)

    # طبقة أعمدة ثلاثية الأبعاد تمثل صفوف الألواح بدقة بصرية عالية
    panel_layer = pdk.Layer(
        "ColumnLayer",
        data=df_panels,
        get_position=["lon", "lat"],
        get_elevation="elevation",
        elevation_scale=12.0,
        radius=180,
        get_fill_color="color",
        pickable=True,
        auto_highlight=True,
    )

    view_state = pdk.ViewState(
        latitude=lat,
        longitude=lon,
        zoom=13,
        pitch=55.0, # زاوية ميل كاميرا ثلاثية الأبعاد واضحة للألواح
        bearing=30
    )

    r_panels = pdk.Deck(
        layers=[panel_layer],
        initial_view_state=view_state,
        tooltip={
            "html": "<b>صف الألواح:</b> {row_name} <br/> <b>قدرة اللوحة:</b> {wattage}W <br/> <b>نسبة الغبار والرمال:</b> {soiling}% <br/> <b>زاوية الميل:</b> {tilt}°",
            "style": {"backgroundColor": "#1e293b", "color": "white", "border": "1px solid #38bdf8"}
        }
    )

    st.pydeck_chart(r_panels)
    st.caption("💡 الخريطة تعرض صفوف الألواح الحية في مرمول. الأعمدة الحمراء تشير إلى صفوف مغطاة بالرمال وتتطلب تدخلاً فورياً للروبوتات الجافة.")

    # جدول تفصيلي مرئي لصفوف الألواح
    st.markdown("### 📊 جدول المواصفات المرئية لصفوف الألواح في مرمول")
    panel_table_data = []
    for i in range(num_strings):
        str_name = f"Solar Row {i+1}"
        cfg = string_configs[str_name]
        robots_assigned = int(total_robots / num_strings)
        status_text = "🚨 اكتساح رملي - مسح روبوتي طارئ" if dust_storm_active else ("🔄 تنظيف جاف نشط" if i%2==0 else "🅿️ وضع الاستعداد")
        
        panel_table_data.append({
            'صف الألواح': str_name,
            'نوع التكنولوجيا': technology_type,
            'قدرة اللوحة': f"{panel_wattage} W",
            'زاوية الميل (Tilt)': f"{cfg['tilt']}°",
            'نسبة الرمال (Soil)': f"{cfg['soiling']}%",
            'الروبوتات المخصصة': robots_assigned,
            'حالة التنظيف': status_text
        })
    st.table(pd.DataFrame(panel_table_data))

with tab4:
    st.subheader("💰 التحليل المالي واقتصاديات أسطول روبوتات مرمول (LCOE & CAPEX)")
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)
    f_col1.metric("استثمار الروبوتات", f"{initial_robot_capex:,.0f} ر.ع")
    f_col2.metric("إهلاك الصيانة اليومي", f"{daily_robot_depreciation:.2f} ر.ع")
    f_col3.metric("صافي العائد اليومي", f"{net_robotic_roi:,.2f} ر.ع")
    f_col4.metric("تكلفة LCOE", f"{lcoe:.4f} ر.ع")

    st.markdown("---")
    col_fin1, col_fin2 = st.columns(2)
    with col_fin1:
        st.markdown("#### 📊 جدوى التنظيف الجاف في صحراء الوسطى")
        st.write("- **تكلفة المياه:** 0.00 ر.ع (استنزاف المياه مستحيل في مرمول؛ التنظيف الجاف بالروبوتات هو الخيار التشغيلي الوحيد).")
        st.write(f"- **تكلفة تشغيل الأسطول السنوية:** {(daily_robot_depreciation * 365):,.2f} ر.ع.")
    with col_fin2:
        st.markdown("#### 💡 العائد الاستثماري (ROI)")
        if net_robotic_roi > 0:
            st.success(f"✅ الروبوتات توفر دخلاً صافياً قدره **{net_robotic_roi:,.2f} ر.ع يومياً** عبر منع تدهور إنتاجية الألواح بسبب رمال الوسطى.")
        else:
            st.warning("⚠️ يُوصى بزيادة تردد دورات المسح نظراً لشدة تراكم الرمال.")

with tab5:
    st.subheader("📋 توليد أوامر الشغل الآلية لموقع مرمول")
    selected_row_wo = st.selectbox("اختر صف الألواح لإصدار أمر العمل", [f"Solar Row {i+1}" for i in range(num_strings)])
    work_order_id = f"WO-MARMOUL-2026-{np.random.randint(1000, 9999)}"
    
    wo_payload = {
        "work_order_id": work_order_id,
        "facility": site_name,
        "target_string": selected_row_wo,
        "panel_type": technology_type,
        "wattage": f"{panel_wattage}W",
        "soil_percentage": string_configs[selected_row_wo]['soiling'],
        "tilt_angle": string_configs[selected_row_wo]['tilt'],
        "priority": "CRITICAL" if dust_storm_active or string_configs[selected_row_wo]['soiling'] > 20 else "NORMAL",
        "assigned_robots": int(total_robots / num_strings),
        "action_required": "Deploy dry-cleaning robot swarm for emergency glass surface sweep and string inspection."
    }

    st.json(wo_payload)
    if st.button("تصدير وإرسال أمر الشغل لفرق الصيانة بمرمول"):
        st.success(f"✅ تم إصدار أمر الشغل رقم **{work_order_id}** بنجاح وإرساله للفرق الميدانية في مرمول!")

st.markdown("---")
st.subheader("🤖 تقرير تحليل الأصول والعمليات المؤسسية (Gemini 3.6)")

if not gemini_api_key:
    st.warning("⚠️ يرجى إدخال مفتاح Gemini API Key في الشريط الجانبي لتفعيل الوكيل الذكي.")
else:
    if st.button("توليد التقرير التشغيلي الشامل لموقع مرمول"):
        with st.spinner("الوكيل الذكي يحلل بيانات صفوف الألواح، الخريطة ثلاثية الأبعاد، وطوارئ صحراء الوسطى..."):
            try:
                client = genai.Client(api_key=gemini_api_key)
                prompt = f"""
                أنت الرئيس التنفيذي للعمليات الهندسية وخبير إدارة محطات الطاقة الشمسية الكبرى.
                بيانات المحطة:
                - الموقع: {site_name} (محافظة الوسطى، عمان)
                - القدرة الكلية: {total_capacity_mw} MW.
                - نوع الألواح: {technology_type} بقدرة {panel_wattage}W.
                - أسراب الروبوتات: {total_robots} روبوت تنظيف جاف.
                - حالة العاصفة: {"نشطة" if dust_storm_active else "غير نشطة"}
                - LCOE: {lcoe:.4f} / kWh.
                
                قدم تقريراً تشغيلياً واقتصادياً متعمقاً باللغة العربية للإدارة العليا حول استقرار مصفوفات الألواح في بيئة مرمول القاسية، أداء الروبوتات الجافة، وكفاءة زوايا الميل.
                """
                interaction = client.interactions.create(model='gemini-3.6-flash', input=prompt)
                st.success("تم توليد التقرير بنجاح!")
                st.markdown(interaction.output_text)
            except Exception as e:
                st.error(f"حدث خطأ أثناء الاتصال: {e}")
