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
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}))
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}))
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}))
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}))

        # 画像生成 (セッションのhighlight_textを使ってマーカーを描画)
        display_images = []
        if uploaded_file.type == "application/pdf":
            display_images = get_pdf_images_with_highlight(file_bytes, st.session_state['highlight_text'])
        else:
            img = Image.open(io.BytesIO(file_bytes))
            display_images = [img]

        # 表示
        with st.container(height=800):
            current_width = int(800 * (st.session_state['zoom_level'] / 100))
            for img in display_images:
                if st.session_state['rotation']:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=current_width)

    # --- 右カラム: 結果と操作 ---
    with col2:
        if st.button("🚀 抽出実行", type="primary", use_container_width=True):
            try:
                model = genai.GenerativeModel(selected_model_name)
                
                # AIに見せる用画像 (マーカーなし、回転適用済み)
                inputs = []
                if uploaded_file.type == "application/pdf":
                    raw_images = get_pdf_images_with_highlight(file_bytes, None) # ハイライトなし
                else:
                    raw_images = [Image.open(io.BytesIO(file_bytes))]
                
                for img in raw_images:
                    if st.session_state['rotation']:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                    inputs.append(img)

                prompt = """
                この請求書画像を解析し、JSON形式で出力してください。Markdownは不要。
                項目: tax_type(税込/税抜), items[日付, 燃料名, 使用量(数値), 請求額(数値)]
                合計行は除外してください。
                出力例: {"tax_type": "税込", "items": [{"日付": "01-01", "燃料名": "軽油", "使用量": 50, "請求額": 8000}]}
                """
                
                with st.spinner("AI解析中..."):
                    res = model.generate_content([prompt] + inputs)
                    text = res.text.replace("```json", "").replace("```", "").strip()
                    if text.startswith("JSON"): text = text[4:]
                    data = json.loads(text)
                    
                    st.session_state['df'] = pd.DataFrame(data["items"])
                    st.session_state['tax_type'] = data.get("tax_type", "不明")
                    st.session_state['highlight_text'] = [] # ハイライトリセット
                    st.toast("完了しました！", icon="✅")

            except Exception as e:
                st.error(f"エラー: {e}")

        # --- 結果表示 & インタラクティブテーブル ---
        if not st.session_state['df'].empty:
            df = st.session_state['df']
            
            # 数値変換
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

            st.markdown(f"**💰 消費税:** `{st.session_state['tax_type']}`")
            
            # 合計表示
            total_cost = df["請求額"].sum()
            total_usage = df["使用量"].sum()
            st.metric("合計請求額", f"¥{total_cost:,.0f}", f"{total_usage:,.2f} L")
            
            st.markdown("---")
            st.caption("👇 行をクリックすると、左のPDFで該当箇所が赤枠で表示されます。")

            # 行選択機能付きデータエディタ
            event = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                key="editor",
                selection_mode="single-row", # 行選択モードを有効化
                on_change=None,
                column_config={
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )

            # --- 選択イベントの処理 ---
            # 選択された行があるかチェック
            if len(event.selection["rows"]) > 0:
                selected_index = event.selection["rows"][0]
                selected_row = df.iloc[selected_index]
                
                # 検索したいキーワードをリスト化 (日付、金額、燃料名)
                # 金額は "5,000" のようなカンマ区切り対策で int化してから文字列に
                targets = [
                    str(selected_row["日付"]),
                    str(int(selected_row["請求額"])), 
                    str(selected_row["燃料名"])
                ]
                
                # 状態が変わった場合のみリラン (無限ループ防止)
                if st.session_state['highlight_text'] != targets:
                    st.session_state['highlight_text'] = targets
                    st.rerun() # 画面を再描画してマーカーを反映
            
            else:
                # 選択解除されたらマーカーを消す
                if st.session_state['highlight_text']:
                    st.session_state['highlight_text'] = []
                    st.rerun()

            # CSVダウンロード
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSV保存", csv, "fuel_data.csv", "text/csv", use_container_width=True)
