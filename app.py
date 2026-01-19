import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageDraw
import pandas as pd
import json
import io
import fitz  # PyMuPDF

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Complete)")
st.title("⛽ 燃料明細 自動抽出ツール (Complete版)")

# --- CSS: 選択行のハイライト調整など ---
st.markdown("""
    <style>
    .stButton button { font-weight: bold; }
    /* 合計表のデザイン */
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
if 'highlight_text' not in st.session_state: st.session_state['highlight_text'] = []

# --- 関数: PDFを画像化 + ハイライト処理 ---
def get_pdf_images(file_bytes, texts_to_highlight=None):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    
    for page in doc:
        # ハイライト処理 (検索して矩形を描画)
        if texts_to_highlight:
            for text in texts_to_highlight:
                if text and len(str(text)) > 1: # 1文字以下の誤検出回避
                    # テキストを検索 (完全一致ではなく部分一致)
                    quads = page.search_for(str(text))
                    # 赤い枠を描画
                    for quad in quads:
                        page.draw_rect(quad, color=(1, 0, 0), width=3) # 赤色、太さ3

        # 画像化 (高画質)
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)", type=["pdf", "png", "jpg"])

# ファイル変更時にリセット
if uploaded_file:
    file_id = uploaded_file.name + str(uploaded_file.size)
    if 'last_file_id' not in st.session_state or st.session_state['last_file_id'] != file_id:
        st.session_state['last_file_id'] = file_id
        st.session_state['df'] = pd.DataFrame()
        st.session_state['highlight_text'] = []
        st.session_state['tax_type'] = "ー"

if uploaded_file and api_key and selected_model_name:
    file_bytes = uploaded_file.read()
    
    # レイアウト: 左(PDF) vs 右(表)
    col1, col2 = st.columns([1.5, 1])

    # --- 左カラム: ビューア ---
    with col1:
        # コントロール
        c1, c2, c3, c4, c5, _ = st.columns([1,1,1,1,1,5])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}))
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}))
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}))
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}))
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}))

        # 画像生成 (ハイライト付き)
        # Session Stateにある「ハイライトしたい文字リスト」を渡す
        if uploaded_file.type == "application/pdf":
            display_images = get_pdf_images(file_bytes, st.session_state['highlight_text'])
        else:
            img = Image.open(io.BytesIO(file_bytes))
            display_images = [img]

        # 表示 (スクロールコンテナ)
        with st.container(height=800):
            width = int(800 * (st.session_state['zoom_level'] / 100))
            for img in display_images:
                if st.session_state['rotation']:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=width)

    # --- 右カラム: 操作 & 結果 ---
    with col2:
        if st.button("🚀 抽出実行", type="primary", use_container_width=True):
            try:
                model = genai.GenerativeModel(selected_model_name)
                # 画像準備
                inputs = []
                if uploaded_file.type == "application/pdf":
                    # 解析用はハイライトなしのクリーンな画像を使う
                    inputs = get_pdf_images(file_bytes, None) 
                else:
                    inputs = [Image.open(io.BytesIO(file_bytes))]
                
                # 回転適用
                if st.session_state['rotation']:
                    inputs = [img.rotate(st.session_state['rotation'], expand=True) for img in inputs]

                prompt = """
                請求書画像を解析し、以下のJSONのみ出力してください。Markdown不要。
                1. **items**: 日付, 燃料名, 使用量(L), 請求額(円) のリスト
                   - 軽油税も行として抽出。合計行は除外。
                2. **tax**: "税込" or "税抜"
                
                Format: {"tax": "...", "items": [{"日付": "MM-DD", "燃料名": "...", "使用量": 0, "請求額": 0}]}
                """
                
                with st.spinner("解析中..."):
                    res = model.generate_content([prompt] + inputs)
                    text = res.text.replace("```json", "").replace("```", "").strip()
                    data = json.loads(text if not text.startswith("JSON") else text[4:])
                    
                    st.session_state['df'] = pd.DataFrame(data["items"])
                    st.session_state['tax_type'] = data.get("tax", "不明")
                    st.session_state['highlight_text'] = [] # ハイライトリセット
                    st.toast("抽出完了", icon="✅")

            except Exception as e:
                st.error(f"エラー: {e}")

        # --- 結果表示 & 編集エリア ---
        if not st.session_state['df'].empty:
            df = st.session_state['df']
            
            # 型変換
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

            # 1. 合計表 (DataFrameで表示して見やすく)
            st.markdown(f"**💰 消費税区分:** `{st.session_state.get('tax_type')}`")
            
            # 集計データの作成
            summary_df = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
            # 合計行を追加
            total_row = pd.DataFrame({
                "燃料名": ["合計"],
                "使用量": [summary_df["使用量"].sum()],
                "請求額": [summary_df["請求額"].sum()]
            })
            summary_display = pd.concat([summary_df, total_row], ignore_index=True)
            
            st.markdown("##### 📊 集計サマリ")
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
            st.markdown("##### 📝 詳細編集 (行選択でPDFハイライト)")
            
            # 2. 編集用テーブル (ここが重要！)
            edited_df = st.data_editor(
                df,
                num_rows="dynamic",     # 行追加・削除可能
                use_container_width=True,
                hide_index=True,
                key="editor",           # 状態管理キー
                on_change=None,         # 自動更新
                selection_mode="single-row", # 行選択モード(v1.35+)
                column_config={
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                    "日付": st.column_config.TextColumn(),
                    "燃料名": st.column_config.TextColumn(),
                }
            )

            # 3. 編集結果をセッションに反映 (次回の再計算のため)
            if not edited_df.equals(st.session_state['df']):
                st.session_state['df'] = edited_df
                st.rerun() # リロードして合計を更新

            # 4. 行選択検知 & ハイライト処理
            # data_editorのselection stateを取得
            if "editor" in st.session_state and st.session_state.editor.get("selection"):
                selection = st.session_state.editor["selection"]
                if selection.get("rows"):
                    row_idx = selection["rows"][0]
                    # 選択された行のデータを取得
                    if row_idx < len(edited_df):
                        selected_row = edited_df.iloc[row_idx]
                        
                        # 検索したいキーワード（日付、金額、燃料名）
                        # ※金額はカンマが入っていると検索できないので文字列化
                        targets = [
                            str(selected_row["日付"]),
                            str(int(selected_row["請求額"])), 
                            str(selected_row["燃料名"])
                        ]
                        
                        # ハイライトリストが変更されたら再描画
                        if st.session_state['highlight_text'] != targets:
                            st.session_state['highlight_text'] = targets
                            st.rerun()
            else:
                # 選択解除されたらハイライトも消す
                if st.session_state['highlight_text']:
                    st.session_state['highlight_text'] = []
                    st.rerun()

            # CSVダウンロード
            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)
# update
