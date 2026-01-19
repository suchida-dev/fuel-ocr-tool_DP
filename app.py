import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
import re

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Safe)")
st.title("⛽ 燃料明細 自動抽出ツール")
st.caption(f"System Version: {st.__version__}")

# --- CSS ---
st.markdown("""
    <style>
    .stButton button { font-weight: bold; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    </style>
""", unsafe_allow_html=True)

# --- 1. APIキー ---
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("✅ 認証済み")
else:
    api_key_input = st.sidebar.text_input("Gemini API Key", type="password")
    api_key = api_key_input.strip() if api_key_input else None

# --- 2. モデル ---
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
    selected_model_name = st.sidebar.selectbox("使用モデル", available_model_names, index=0)

# --- 3. セッション初期化 ---
if 'zoom_level' not in st.session_state: st.session_state['zoom_level'] = 100
if 'rotation' not in st.session_state: st.session_state['rotation'] = 0
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'highlight_text' not in st.session_state: st.session_state['highlight_text'] = []
if 'last_file_id' not in st.session_state: st.session_state['last_file_id'] = None

# --- 関数: PDF画像化 ---
def get_pdf_images(file_bytes, texts_to_highlight=None):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        if texts_to_highlight:
            for text in texts_to_highlight:
                if text and len(str(text)) > 0:
                    quads = page.search_for(str(text))
                    for quad in quads:
                        page.draw_rect(quad, color=(1, 0, 0), width=0, fill=(1, 0, 0), fill_opacity=0.3)
                        page.draw_rect(quad, color=(1, 0, 0), width=1.5)
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- 関数: JSON抽出 ---
def extract_json(text):
    try:
        return json.loads(text)
    except:
        pass
    try:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1:
            json_str = text[start:end]
            return json.loads(json_str)
    except:
        pass
    return None

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg"])

if uploaded_file:
    file_id = uploaded_file.name + str(uploaded_file.size)
    if st.session_state['last_file_id'] != file_id:
        st.session_state['last_file_id'] = file_id
        st.session_state['df'] = pd.DataFrame()
        st.session_state['highlight_text'] = []
        st.session_state['tax_type'] = "ー"
        st.session_state['zoom_level'] = 100
        st.session_state['rotation'] = 0

if uploaded_file and api_key and selected_model_name:
    file_bytes = uploaded_file.read()
    col1, col2 = st.columns([1.5, 1])

    with col1:
        c1, c2, c3, c4, c5, _ = st.columns([1,1,1,1,1,5])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}), help="拡大")
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}), help="縮小")
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}), help="右回転")
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}), help="左回転")
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}), help="リセット")

        display_images = []
        if uploaded_file.type == "application/pdf":
            display_images = get_pdf_images(file_bytes, st.session_state['highlight_text'])
        else:
            img = Image.open(io.BytesIO(file_bytes))
            display_images = [img]

        with st.container(height=800):
            current_width = int(800 * (st.session_state['zoom_level'] / 100))
            for img in display_images:
                if st.session_state['rotation']:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=current_width)

    with col2:
        if st.button("🚀 抽出実行", type="primary", use_container_width=True):
            try:
                model = genai.GenerativeModel(selected_model_name)
                inputs = []
                if uploaded_file.type == "application/pdf":
                    raw_images = get_pdf_images(file_bytes, None)
                else:
                    raw_images = [Image.open(io.BytesIO(file_bytes))]
                for img in raw_images:
                     if st.session_state['rotation']:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                     inputs.append(img)

                prompt = """
                請求書画像を解析し、以下の情報をJSON形式のみで出力してください。Markdown不要。
                
                1. **items**: 以下のリスト
                   - 日付 (MM-DD)
                   - 燃料名 (ガソリン, 軽油, 灯油, 重油, 軽油税などCO2排出対象のみ。洗車等は除外)
                   - 使用量 (L) 数値
                   - 請求額 (円) 数値
                2. **tax**: "税込" or "税抜"
                """
                
                with st.spinner("解析中..."):
                    res = model.generate_content(
                        [prompt] + inputs,
                        generation_config=genai.types.GenerationConfig(max_output_tokens=2000)
                    )
                    data = extract_json(res.text)
                    
                    if data:
                        # --- ここでKeyError対策を実施 ---
                        df_new = pd.DataFrame(data.get("items", []))
                        
                        # 必須カラムを定義
                        required_cols = ["日付", "燃料名", "使用量", "請求額"]
                        
                        # データが空の場合でもカラムだけは作る
                        if df_new.empty:
                            df_new = pd.DataFrame(columns=required_cols)
                        
                        # 不足しているカラムがあれば初期値で埋める
                        for col in required_cols:
                            if col not in df_new.columns:
                                if col in ["使用量", "請求額"]:
                                    df_new[col] = 0
                                else:
                                    df_new[col] = ""
                                    
                        st.session_state['df'] = df_new
                        st.session_state['tax_type'] = data.get("tax", "不明")
                        st.session_state['highlight_text'] = []
                        st.toast("完了", icon="✅")
                    else:
                        st.error("解析失敗: AIの応答形式が不正です。再試行してください。")

            except Exception as e:
                st.error(f"エラー: {e}")

        if not st.session_state['df'].empty:
            df = st.session_state['df']
            df.reset_index(drop=True, inplace=True)
            
            # 安全に数値変換（カラムが存在することが保証されているためエラーにならない）
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

            st.markdown(f"**💰 消費税:** `{st.session_state.get('tax_type')}`")

            summary_df = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
            total_row = pd.DataFrame({
                "燃料名": ["🔴 合計"],
                "使用量": [summary_df["使用量"].sum()],
                "請求額": [summary_df["請求額"].sum()]
            })
            st.dataframe(
                pd.concat([summary_df, total_row], ignore_index=True),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )

            st.markdown("---")
            st.markdown("##### 📝 詳細データ")

            edited_df = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                key="editor_safe",
                selection_mode="single-row",
                column_config={
                    "日付": st.column_config.TextColumn(),
                    "燃料名": st.column_config.TextColumn(),
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )
            
            if "editor_safe" in st.session_state and st.session_state.editor_safe.get("selection"):
                selection = st.session_state.editor_safe["selection"]
                if selection.get("rows"):
                    row_idx = selection["rows"][0]
                    if row_idx < len(edited_df):
                        selected_row = edited_df.iloc[row_idx]
                        targets = [
                            str(selected_row["日付"]),
                            str(int(selected_row["請求額"])), 
                            str(selected_row["燃料名"])
                        ]
                        if st.session_state['highlight_text'] != targets:
                            st.session_state['highlight_text'] = targets
                            st.rerun()
            else:
                if st.session_state['highlight_text']:
                    st.session_state['highlight_text'] = []
                    st.rerun()

            if not edited_df.equals(st.session_state['df']):
                st.session_state['df'] = edited_df
                st.rerun() 

            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)
