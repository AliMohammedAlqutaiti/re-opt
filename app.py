import streamlit as st
import pandas as pd
import numpy as np
import pvlib
import requests
import pydeck as pdk
from google import genai

st.set_page_config(page_title="RE-OPT: Marmoul 3D Geo-Spatial Twin", layout="wide")

st.title("⚡ RE-OPT: Marmoul Desert Geo-Spatial Digital Twin")
st.markdown("التوأم الرقمي الجغرافي المؤسسي - خريطة حية تفاعلية لصحراء مرمول (الوسطى) مع التحكم الميداني المباشر بأسراب الألواح.")

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

# توليد إحداثيات جغرافية موزعة حول موقع مرمول الحقيقي على الخريطة
np.random.seed(42)
map_data = []
for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
    # توزيع الحقول جغرافياً بمسافات صغيرة حول مركز مرمول
    lat_offset = np.random.uniform(-0.03, 0.03)
    lon_offset = np.random.uniform(-0.03, 0.03)
    is_critical = cfg['soiling'] > 12.0 or dust_storm_active
    
    map_data.append({
        "name": inv_name,
        "lat": MARMOUL_LAT + lat_offset,
        "lon": MARMOUL_LON + lon_offset,
        "soiling": cfg['soiling'],
        "status": "Critical Dust" if is_critical else "Normal Optimal",
        "color": [239, 68, 68, 220] if is_critical else [14, 165, 233, 220],
        "elevation": 1200
    })

df_map = pd.DataFrame(map_data)

tab1, tab2, tab3 = st.tabs([
    "🗺️ الخريطة الجغرافية الحية لصحراء مرمول", 
    "📊 تحليل الأداء المالي والتشغيلي", 
    "📋 الأوامر والتقارير الذكية"
])

with tab1:
    st.subheader("📍 التوزيع الجغرافي لحقول الطاقة الشمسية في صحراء مرمول (الوسطى)")
    st.markdown("خريطة تفاعلية حية مرتبطة بإحداثيات الصحراء الحقيقية؛ توضح الحقول باللون الأزرق للحالة الطبيعية أو الأحمر عند ترسب الرمال وارتفاع خطورة التلوث:")

    # إعداد طبقات PyDeck لعرض الخريطة التفاعلية الصحراوية
    layer = pdk.Layer(
        "ColumnLayer",
        data=df_map,
        get_position=["lon", "lat"],
        get_elevation="soiling",
        elevation_scale=150,
        radius=800,
        get_fill_color="color",
        pickable=True,
        auto_highlight=True,
    )

    view_state = pdk.ViewState(
        latitude=MARMOUL_LAT,
        longitude=MARMOUL_LON,
        zoom=9,
        pitch=45,
        bearing=15
    )

    r = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        map_style="mapbox://styles/mapbox/satellite-v9", # مظهر خريطة الأقمار الصناعية المناسب للصحراء
        tooltip={"text": "الحقل: {name}\nالحالة: {status}\nنسبة الغبار: {soiling}%"}
    )

    st.pydeck_chart(r)
    
    st.info("💡 **ملاحظة:** يمكنك تحريك الخريطة، التكبير/التصغير، أو إمالة الرؤية ثلاثية الأبعاد لاستكشاف تضاريس صحراء مرمول ومواقع محولات الطاقة.")

with tab2:
    st.subheader("📊 مؤشرات الطاقة الفورية والاقتصاديات")
    c1, c2, c3 = st.columns(3)
    c1.metric("موقع المحطة الجغرافي", "محافظة الوسطى، مرمول")
    c2.metric("درجة الحرارة الحالية", f"{live_temp}°C")
    c3.metric("سرعة الرياح", f"{live_wind} m/s")

with tab3:
    st.subheader("🤖 تقرير الوكيل الذكي للعمليات")
    if gemini_api_key and st.button("توليد تحليل الخريطة الجغرافية والمخاطر"):
        try:
            client = genai.Client(api_key=gemini_api_key)
            response = client.interactions.create(
                model='gemini-3.6-flash',
                input=f"قدم تحليلاً استراتيجياً لإدارة حقول الطاقة الشمسية الموزعة جغرافياً في صحراء مرمول بعمان، مع تقييم تأثير العواصف الرملية وحالة الغبار الحالية."
            )
            st.markdown(response.output_text)
        except Exception as e:
            st.error(f"خطأ في الاتصال: {e}")
    elif not gemini_api_key:
        st.warning("يرجى إدخال مفتاح Gemini API في الشريط الجانبي.")
