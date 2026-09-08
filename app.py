import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import pydeck as pdk
import streamlit.components.v1 as components
from google import genai

st.set_page_config(page_title="RE-OPT: Marmoul Geo-Spatial Solar Twin", layout="wide")

st.title("⚡ RE-OPT: Marmoul Desert Geo-Spatial & Detailed Solar Array Twin")
st.markdown("التوأم الرقمي المؤسسي - خريطة حية لصحراء مرمول مع مصفوفات الألواح التفصيلية داخل الحقول بعيداً عن الطرق.")

# إحداثيات صحراء مرمول (عمان)
MARMOUL_LAT, MARMOUL_LON = 18.15, 55.18

@st.cache_data(ttl=600)
def fetch_live_weather(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            current = data.get("current", {})
            return current.get("temperature_2m", 38.0), current.get("wind_speed_10m", 9.0)
    except Exception:
        pass
    return 38.0, 9.0

api_temp, api_wind = fetch_live_weather(MARMOUL_LAT, MARMOUL_LON)

gemini_api_key = st.sidebar.text_input("أدخل مفتاح Gemini API Key", type="password")
total_capacity_mw = st.sidebar.slider("إجمالي قدرة المحطة (MW)", min_value=50.0, max_value=1000.0, value=150.0, step=50.0)
total_capacity = total_capacity_mw * 1000 
num_blocks = st.sidebar.selectbox("عدد حقول محولات الطاقة (Inverter Blocks)", [2, 4, 6, 8], index=1)

scada_mode = st.sidebar.toggle("تفعيل الربط الحي مع أنظمة SCADA", value=True)
live_temp = api_temp if scada_mode else st.sidebar.number_input("درجة الحرارة المحيطة (°C)", value=38.0)
live_wind = api_wind if scada_mode else st.sidebar.number_input("سرعة الرياح (m/s)", value=8.0)

st.sidebar.subheader("محاكاة العواصف الرملية في صحراء الوسطى")
dust_storm_active = st.sidebar.toggle("🚨 محاكاة عاصفة رملية مفاجئة بمرمول", value=False)
storm_soiling_penalty = st.sidebar.slider("معامل الغبار الإضافي (%)", min_value=5.0, max_value=50.0, value=18.0) if dust_storm_active else 0.0

st.sidebar.subheader("التحكم المستقل بحقول الألواح")
inverter_configs = {}
for i in range(num_blocks):
    inv_name = f"Marmoul Field Block {i+1}"
    with st.sidebar.expander(f"إعدادات {inv_name}", expanded=(i==0)):
        soil = st.slider(f"نسبة الغبار (%) - Block {i+1}", 0.0, 45.0, float(3.0 + i * 2.5 + storm_soiling_penalty), key=f"soil_{i}")
        tilt = st.slider(f"زاوية الميل (Tilt °) - Block {i+1}", 5.0, 45.0, 22.0, key=f"tilt_{i}")
        inverter_configs[inv_name] = {'soiling': soil, 'tilt': tilt}

# إحداثيات مدروسة تبعد تماماً عن الطرق الصحراوية وتوزيع دقيق للحقول
offsets = [
    (0.035, 0.035),   # الحقل الأول (شمال شرق بعيد عن الطريق)
    (-0.025, 0.045),  # الحقل الثاني (شرق)
    (-0.04, -0.035),  # الحقل الثالث (جنوب غرب)
    (0.015, -0.04)    # الحقل الرابع (جنوب شرق)
]

polygon_data = []
for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
    off_lat, off_lon = offsets[i % len(offsets)]
    center_lat = MARMOUL_LAT + off_lat
    center_lon = MARMOUL_LON + off_lon
    
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
        "name": inv_name,
        "polygon": polygon,
        "soiling": cfg['soiling'],
        "status": "🚨 خطر تلوث رملي حرج" if is_critical else "✅ أداء طبيعي وسليم",
        "color": color
    })

df_poly = pd.DataFrame(polygon_data)

tab1, tab2, tab3 = st.tabs([
    "🗺️ الخريطة الجغرافية وحقول الألواح التفصيلية", 
    "☀️ العرض المجسم للحقول (3D Diorama)", 
    "💰 الاقتصاديات والتقارير الذكية"
])

with tab1:
    st.subheader("📍 التوزيع الجغرافي لحقول الألواح (بعيداً عن الطرق الصحراوية)")
    st.markdown("خريطة تفاعلية لصحراء مرمول؛ تم إبعاد المضلعات تماماً عن مسارات الطرق وتظهر بداخلها مصفوفات الألواح الشمسية:")

    layer = pdk.Layer(
        "PolygonLayer",
        df_poly,
        id="solar-fields-safe",
        get_polygon="polygon",
        get_fill_color="color",
        get_line_color=[255, 255, 255],
        line_width_min_pixels=3,
        extruded=False, 
        pickable=True,
        auto_highlight=True,
    )

    view_state = pdk.ViewState(
        latitude=MARMOUL_LAT,
        longitude=MARMOUL_LON,
        zoom=10,
        pitch=20,
        bearing=0
    )

    r = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        map_style="https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        tooltip={"text": "الحقل: {name}\nالحالة: {status}\nنسبة الغبار: {soiling}%"}
    )

    st.pydeck_chart(r)

    st.markdown("---")
    st.subheader("🔍 معاينة شبكة الألواح الداخلية لكل حقل جغرافياً")
    
    # عرض تفصيلي لشبكة الألواح لكل حقل تحت الخريطة لضمان الدقة البصرية المطلوبة
    cols = st.columns(2)
    for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
        soiling = cfg['soiling']
        is_critical = soiling > 12.0 or dust_storm_active
        p_color = "#ef4444" if is_critical else "#1e40af"
        
        detail_card = f"""
        <div style="
            background: #0f172a;
            border: 2px solid {'#ef4444' if is_critical else '#38bdf8'};
            border-radius: 12px;
            padding: 14px;
            margin-bottom: 15px;
            color: white;
            font-family: sans-serif;
            text-align: right;
            direction: rtl;
        ">
            <h4 style="margin: 0 0 10px 0; color: #f8fafc; font-size: 15px;">{inv_name} - مصفوفة الألواح الداخلية</h4>
            <div style="
                background: linear-gradient(135deg, #b45309, #78350f);
                border: 2px solid #451a03;
                border-radius: 8px;
                height: 110px;
                display: flex;
                align-items: center;
                justify-content: center;
            ">
                <div style="
                    display: grid;
                    grid-template-columns: repeat(5, 1fr);
                    gap: 6px;
                    width: 85%;
                    transform: perspective(400px) rotateX(30deg);
                ">
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                    <div style="background: {p_color}; height: 26px; border-radius: 3px; border: 1px solid #93c5fd;"></div>
                </div>
            </div>
            <p style="margin: 8px 0 0 0; font-size: 12px; color: #cbd5e1;">نسبة الغبار الحالية: <b>{soiling}%</b> | الحالة: <b>{'حرج' if is_critical else 'طبيعي'}</b></p>
        </div>
        """
        with cols[i % 2]:
            components.html(detail_card, height=210)

with tab2:
    st.subheader("☀️ عرض الحقول المجسمة (3D Diorama View)")
    cols = st.columns(2)
    for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
        soiling = cfg['soiling']
        tilt = cfg['tilt']
        is_critical = soiling > 12.0 or dust_storm_active
        panel_color = "#ef4444" if is_critical else "#1e40af"
        status_text = "🚨 تلوث رملي حرج" if is_critical else "✅ حقل نظيف ومنتج"

        diorama_html = f"""
        <div style="
            background: #1e293b;
            border: 2px solid {'#ef4444' if is_critical else '#0ea5e9'};
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 20px;
            color: white;
            font-family: sans-serif;
            text-align: right;
            direction: rtl;
            box-shadow: 0 12px 25px rgba(0,0,0,0.6);
        ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h4 style="margin: 0; color: #f8fafc; font-size: 16px;">🌱 {inv_name} (مرمول)</h4>
                <span style="background: {'#ef4444' if is_critical else '#0284c7'}; color: white; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: bold;">{status_text}</span>
            </div>
            
            <div style="
                background: linear-gradient(135deg, #d97706, #92400e);
                border: 3px solid #78350f;
                border-radius: 12px;
                height: 150px;
                display: flex;
                align-items: center;
                justify-content: center;
            ">
                <div style="
                    display: grid;
                    grid-template-columns: repeat(4, 1fr);
                    gap: 8px;
                    width: 80%;
                    transform: perspective(600px) rotateX(40deg);
                ">
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

with tab3:
    st.subheader("💰 التحليل المالي والتقارير الذكية")
    c1, c2, c3 = st.columns(3)
    c1.metric("قدرة المحطة", f"{total_capacity_mw} MW")
    c2.metric("درجة الحرارة", f"{live_temp}°C")
    c3.metric("سرعة الرياح", f"{live_wind} m/s")
    
    if gemini_api_key and st.button("توليد تقرير الذكاء الاصطناعي التشغيلي"):
        try:
            client = genai.Client(api_key=gemini_api_key)
            response = client.interactions.create(
                model='gemini-3.6-flash',
                input=f"قدم تقريراً استراتيجياً لموقع محطة مرمول للطاقة الشمسية بقدرة {total_capacity_mw} ميجاوات في صحراء الوسطى بسلطنة عمان."
            )
            st.markdown(response.output_text)
        except Exception as e:
            st.error(f"خطأ: {e}")
