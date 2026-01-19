import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Marker)")
st.title("⛽ 燃料明細 自動抽出ツール")

# --- CSS: ボタンデザイン調整 ---
st.markdown("""
    <style>
    .stButton button {
        padding: 0px 10px;
        font-weight: bold;
    }
    /* エキスパンダーのスタイル調整（結果表示用） */
    .streamlit-expanderHeader {
        font-size: 1.1em;
        font-weight: bold;
    }
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

# --- 2. モデル取得 ---
available_model_names = []
if api_key:
    genai.configure(api_key=api_key, transport='rest')
    try:
        # 簡易的に取得
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_model_names.append(m.name)
    except Exception as e:
        pass

selected_model_name = None
if available_model_names:
    selected_model_name = st.sidebar.selectbox("使用するモデル", available_model_names)

# --- 3. セッション状態の初期化 ---
if 'zoom_level' not in st.session_state: st.session_state['zoom_level'] = 100
if 'rotation' not in st.session_state: st.session_state['rotation'] = 0
if 'last_uploaded_file' not in st.session_state: st.session_state['last_uploaded_file'] = None
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'highlight_text' not in st.session_state: st.session_state['highlight_text'] = []

# --- 関数: PDF画像化 + マーカー描画 ---
def pdf_to_all_images(file_bytes, texts_to_highlight=None):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        # ハイライト処理
        if texts_to_highlight:
            for text in texts_to_highlight:
                if text and len(str(text)) > 0:
                    quads = page.search_for(str(text))
                    for quad in quads:
                        # 赤枠を描画
                        page.draw_rect(quad, color=(1, 0, 0), width=3, fill_opacity=0.2, fill=(1, 0.8, 0.8))

        pix = page.get_pixmap(dpi=200)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg", "jpeg"])

# ファイル変更時のリセット処理
if uploaded_file:
    file_id = uploaded_file.name + str(uploaded_file.size)
    if st.session_state['last_uploaded_file'] != file_id:
        st.session_state['last_uploaded_file'] = file_id
        st.session_state['df'] = pd.DataFrame()
        st.session_state['highlight_text'] = []
        if 'tax_type' in st.session_state: del st.session_state['tax_type']
        st.session_state['zoom_level'] = 100
        st.session_state['rotation'] = 0

if uploaded_file and api_key and selected_model_name:
    
    file_bytes = uploaded_file.read()
    
    # 画像生成 (ハイライト情報を反映)
    input_contents = [] 
    if uploaded_file.type == "application/pdf":
        input_contents = pdf_to_all_images(file_bytes, st.session_state['highlight_text'])
    else:
        image = Image.open(io.BytesIO(file_bytes))
        input_contents = [image]

    # --- 画面構成: 左(Viewer) vs 右(Editor) ---
    col1, col2 = st.columns([2, 1])

    with col1:
        # --- コントロールバー ---
        c1, c2, c3, c4, c5, _ = st.columns([1, 1, 1, 1, 1, 6])
        
        def zoom_in(): st.session_state['zoom_level'] += 25
        def zoom_out(): st.session_state['zoom_level'] = max(10, st.session_state['zoom_level'] - 25)
        def rotate
