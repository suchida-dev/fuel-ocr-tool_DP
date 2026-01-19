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
        def rotate_right(): st.session_state['rotation'] = (st.session_state['rotation'] - 90) % 360
        def rotate_left(): st.session_state['rotation'] = (st.session_state['rotation'] + 90) % 360
        def reset_view(): 
            st.session_state['zoom_level'] = 100
            st.session_state['rotation'] = 0

        with c1: st.button("➕", on_click=zoom_in, help="拡大", use_container_width=True)
        with c2: st.button("➖", on_click=zoom_out, help="縮小", use_container_width=True)
        with c3: st.button("⤵", on_click=rotate_right, help="右回転", use_container_width=True)
        with c4: st.button("⤴", on_click=rotate_left, help="左回転", use_container_width=True)
        with c5: st.button("R", on_click=reset_view, help="リセット", use_container_width=True)

        # --- 画像表示エリア ---
        with st.container(height=850):
            current_width = int(1000 * (st.session_state['zoom_level'] / 100))
            
            for img in input_contents:
                if st.session_state['rotation'] != 0:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=current_width)

    with col2:
        st.subheader("📊 抽出結果")
        
        # 抽出ボタン
        if st.button("抽出を開始する", type="primary", use_container_width=True):
            st.info(f"処理ページ数: {len(input_contents)}枚 / モデル: {selected_model_name}")
            
            try:
                model = genai.GenerativeModel(selected_model_name)
                
                # AI用画像生成 (マーカーなし)
                processed_inputs = []
                if uploaded_file.type == "application/pdf":
                    base_imgs = pdf_to_all_images(file_bytes, None)
                else:
                    base_imgs = [Image.open(io.BytesIO(file_bytes))]

                for img in base_imgs:
                    if st.session_state['rotation'] != 0:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                    processed_inputs.append(img)
                
                prompt = """
                この請求書画像を解析し、JSON形式で出力してください。Markdownは不要。
                
                1. **items**: 明細リスト (日付, 燃料名, 使用量(L), 請求額(円))
                   - 合計行は除外。
                2. **tax_type**: "税込" または "税抜"
                
                出力例: {"tax_type": "税込", "items": [{"日付": "01-01", "燃料名": "軽油", "使用量": 50, "請求額": 8000}]}
                """
                
                with st.spinner("解析中..."):
                    response = model.generate_content([prompt] + processed_inputs)
                    
                    json_text = response.text.replace("```json", "").replace("```", "").strip()
                    if json_text.startswith("JSON"): json_text = json_text[4:]
                    
                    try:
                        full_data = json.loads(json_text)
                    except:
                        # 簡易的な修復
                        s = json_text.find('{')
                        e = json_text.rfind('}') + 1
                        full_data = json.loads(json_text[s:e])

                    df = pd.DataFrame(full_data.get("items", []))
                    
                    st.session_state['df'] = df
                    st.session_state['tax_type'] = full_data.get("tax_type", "不明")
                    st.session_state['highlight_text'] = []
                    
                    st.toast("完了しました！", icon="✅")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

        # --- 結果の常時表示エリア ---
        if 'df' in st.session_state and not st.session_state['df'].empty:
            df = st.session_state['df']
            tax_type = st.session_state.get('tax_type', '不明')

            # 数値変換
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

            st.markdown(f"**💰 消費税区分:** `{tax_type}`")
            st.markdown("##### ⛽ 燃料別合計")
            
            grouped = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
            for index, row in grouped.iterrows():
                usage_str = f"{row['使用量']:.2f} L" if row['使用量'] > 0 else "-"
                st.info(f"**{row['燃料名']}**: {usage_str} / ¥{row['請求額']:,.0f}")

            st.markdown("---")
            st.caption("👇 行をクリックすると、左のPDFで該当箇所が赤枠で表示されます。")

            # --- マーカー機能付きエディタ ---
            # ここが重要: selection_modeを使うために num_rows="dynamic" を削除しました
            edited_df = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                key="editor_marker", # キーを変更してキャッシュ回避
                selection_mode="single-row", # ★行選択機能を有効化
                column_config={
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )
            
            # 選択イベントの処理
            if "editor_marker" in st.session_state and st.session_state.editor_marker.get("selection"):
                selection = st.session_state.editor_marker["selection"]
                if selection.get("rows"):
                    row_idx = selection["rows"][0]
                    # 範囲チェック
                    if row_idx < len(edited_df):
                        selected_row = edited_df.iloc[row_idx]
                        targets = [
                            str(selected_row["日付"]),
                            str(int(selected_row["請求額"])), 
                            str(selected_row["燃料名"])
                        ]
                        # 変化があればリロード
                        if st.session_state['highlight_text'] != targets:
                            st.session_state['highlight_text'] = targets
                            st.rerun()
            else:
                if st.session_state['highlight_text']:
                    st.session_state['highlight_text'] = []
                    st.rerun()

            # CSVダウンロード
            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)
