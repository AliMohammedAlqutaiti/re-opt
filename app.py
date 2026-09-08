with tab3:
    st.subheader("☀️ العرض البصري ثلاثي الأبعاد لألواح مرمول")
    st.markdown("تصميم بصري احترافي يحاكي شكل اللوحة الشمسية الإطارية ذات التقسيمات الشبكية (Grid)، مع التلوين التلقائي بالأزرق للأداء السليم أو الأحمر عند ترسب الرمال:")

    # عرض البطاقات جنباً إلى جنب بشكل احترافي نظيف
    cols = st.columns(2)
    
    for i, (inv_name, cfg) in enumerate(inverter_configs.items()):
        soiling = cfg['soiling']
        tilt = cfg['tilt']
        is_critical = soiling > 12.0 or dust_storm_active
        
        border_color = "#ef4444" if is_critical else "#3b82f6"
        bg_cells = "#fee2e2" if is_critical else "#eff6ff"
        cell_grid_border = "#fca5a5" if is_critical else "#93c5fd"
        cell_bg = "#fef2f2" if is_critical else "#dbeafe"
        status_text = "🚨 تلوث رملي حرج" if is_critical else "✅ أداء طبيعي نظيف"

        # استخدام HTML نظيف بالكامل مع التأكد من عدم وجود تداخل في الأقواس
        html_code = f"""
        <div style="
            background: #0f172a;
            border: 2px solid {border_color};
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 15px;
            color: white;
            font-family: sans-serif;
            text-align: right;
            direction: rtl;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <h4 style="margin: 0; color: #f8fafc; font-size: 16px;">{inv_name}</h4>
                <span style="background: {border_color}; color: white; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: bold;">{status_text}</span>
            </div>
            
            <div style="
                background: {bg_cells};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 8px;
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                grid-template-rows: repeat(3, 1fr);
                gap: 4px;
                height: 90px;
                margin-bottom: 10px;
            ">
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
                <div style="background: {cell_bg}; border: 1px solid {cell_grid_border}; border-radius: 3px;"></div>
            </div>

            <div style="display: flex; justify-content: space-between; font-size: 13px; background: rgba(255,255,255,0.05); padding: 6px 10px; border-radius: 4px;">
                <span>نسبة الغبار: <b>{soiling}%</b></span>
                <span>زاوية الميل: <b>{tilt}°</b></span>
            </div>
        </div>
        """
        with cols[i % 2]:
            st.markdown(html_code, unsafe_allow_html=True)
