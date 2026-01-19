import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Safe Mode)")
st.title("⛽ 燃料明細 自動抽出ツール")

# --- CSS: デザイン調整 ---
st.markdown("""
    <style>
    .stButton button { font-weight: bold; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    </style>
""", unsafe_allow_html=True)

# --- 1. APIキー設定 ---
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("✅ 認証済み")
else:
    api_key_input = st.sidebar.text_input("Gemini API Key", type="password")
    api_key = api_key_input.strip() if api_key_input else None

# --- 2. モデル設定 ---
available_model_names = []
if api_key:
    genai.configure(api_key=api_key, transport='rest')
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_model_names.append(m.name)
    except: pass

selected_model_name = None
if available_model_names:
    selected_model_name = st.sidebar.selectbox("使用モデル", available_model_names)

# --- 3. セッション初期化 ---
if 'zoom_level' not in st.session_state: st.session_state['zoom_level'] = 100
if 'rotation' not in st.session_state: st.session_state['rotation'] = 0
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'last_file_id' not in st.session_state: st.session_state['last_file_id'] = None

# --- 関数: PDF画像化 ---
def get_pdf_images(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg"])

# ファイル変更時にリセット
if uploaded_file:
    file_id = uploaded_file.name + str(uploaded_file.size)
    if st.session_state['last_file_id'] != file_id:
        st.session_state['last_file_id'] = file_id
        st.session_state['df'] = pd.DataFrame()
        st.session_state['tax_type'] = "ー"
        st.session_state['zoom_level'] = 100
        st.session_state['rotation'] = 0

if uploaded_file and api_key and selected_model_name:
    file_bytes = uploaded_file.read()
    
    col1, col2 = st.columns([1.5, 1])

    # --- 左カラム: ビューア ---
    with col1:
        c1, c2, c3, c4, c5, _ = st.columns([1,1,1,1,1,5])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}), help="拡大")
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}), help="縮小")
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}), help="右回転")
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}), help="左回転")
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}), help="リセット")

        display_images = []
        if uploaded_file.type == "application/pdf":
            display_images = get_pdf_images(file_bytes)
        else:
            img = Image.open(io.BytesIO(file_bytes))
            display_images = [img]

        with st.container(height=800):
            current_width = int(800 * (st.session_state['zoom_level'] / 100))
            for img in display_images:
                if st.session_state['rotation']:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=current_width)

    # --- 右カラム: 操作 & 結果 ---
    with col2:
        if st.button("🚀 抽出実行", type="primary", use_container_width=True):
            try:
                model = genai.GenerativeModel(selected_model_name)
                
                inputs = []
                for img in display_images:
                     if st.session_state['rotation']:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                     inputs.append(img)

                # --- プロンプト (CO2排出対象のみ) ---
                prompt = """
                この請求書画像を解析し、以下の情報をJSON形式のみで出力してください。Markdownコードブロックは不要です。
                
                1. **items**: 以下の項目のリスト
                   - 日付 (MM-DD形式)
                   - 燃料名
                     - **抽出対象**: ガソリン（レギュラー、ハイオク）、軽油、灯油、重油など、**CO2を排出する燃料全般**。
                     - **軽油税**が別行にある場合は、それも必ず抽出してください。
                     - **除外対象**: 洗車、オイル交換、工賃、タイヤ交換代、部品代など、**燃料以外の項目は全て無視**してください。
                   - 使用量 (L) 数値のみ
                   - 請求額 (円) 数値のみ
                   - 明細以外の「合計」行は除外してください。
                2. **tax**: "税込" または "税抜"
                
                出力例:
                {"tax": "税込", "items": [{"日付": "01-15", "燃料名": "ハイオク", "使用量": 45.2, "請求額": 7800}]}
                """
                
                with st.spinner("AIが解析中..."):
                    res = model.generate_content([prompt] + inputs)
                    text = res.text.replace("```json", "").replace("```", "").strip()
                    if text.startswith("JSON"): text = text[4:]
                    data = json.loads(text)
                    
                    st.session_state['df'] = pd.DataFrame(data["items"])
                    st.session_state['tax_type'] = data.get("tax", "不明")
                    
                    st.toast("抽出完了", icon="✅")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

        # --- 結果表示 ---
        if not st.session_state['df'].empty:
            df = st.session_state['df']
            
            # 数値変換
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

            st.markdown(f"**💰 消費税区分:** `{st.session_state.get('tax_type')}`")

            # 集計サマリ
            st.markdown("##### 📊 集計サマリ")
            summary_df = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
            total_usage = summary_df["使用量"].sum()
            total_cost = summary_df["請求額"].sum()
            
            total_row = pd.DataFrame({
                "燃料名": ["🔴 合計"],
                "使用量": [total_usage],
                "請求額": [total_cost]
            })
            summary_display = pd.concat([summary_df, total_row], ignore_index=True)

            st.dataframe(
                summary_display,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )

            st.markdown("---")
            st.markdown("##### 📝 詳細データ")

            # 詳細エディタ (安全版: selection_mode を削除)
            edited_df = st.data_editor(
                df,
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                key="editor",
                column_config={
                    "日付": st.column_config.TextColumn(),
                    "燃料名": st.column_config.TextColumn(),
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )

            # 変更検知 (自動再計算)
            if not edited_df.equals(st.session_state['df']):
                st.session_state['df'] = edited_df
                st.rerun() 

            # CSVダウンロード
            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)
