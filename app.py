import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
import time

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Marker)")
st.title("⛽ 燃料明細 自動抽出ツール")

# --- CSS: デザイン調整 ---
st.markdown("""
    <style>
    .stButton button {
        padding: 0px 10px;
        font-weight: bold;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.2rem;
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
        # モデル取得の頻度を下げるため簡易的な実装
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
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'highlight_text' not in st.session_state: st.session_state['highlight_text'] = [] # マーカー用テキスト
if 'last_file_id' not in st.session_state: st.session_state['last_file_id'] = None

# --- 関数: PDFを画像化し、必要ならマーカーを引く ---
def get_pdf_images_with_highlight(file_bytes, texts_to_highlight=None):
    """
    PDFを画像に変換する。texts_to_highlightがあれば、その箇所に赤枠を描画する。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    
    for page in doc:
        # ハイライト処理 (検索して矩形を描画)
        if texts_to_highlight:
            for text in texts_to_highlight:
                # 文字列型に変換して検索
                if text and len(str(text)) > 0:
                    quads = page.search_for(str(text))
                    for quad in quads:
                        # 赤い枠を描画 (color=(R, G, B), width=線の太さ)
                        page.draw_rect(quad, color=(1, 0, 0), width=4, fill_opacity=0.2, fill=(1, 0.8, 0.8))

        # 画像化 (dpi=150で十分)
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg", "jpeg"])

# ファイルが変更されたらリセット
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
    
    col1, col2 = st.columns([1.5, 1]) # 比率調整

    # --- 左カラム: ビューア ---
    with col1:
        # ツールバー
        c1, c2, c3, c4, c5, _ = st.columns([1, 1, 1, 1, 1, 5])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}))
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st
