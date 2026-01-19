import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
import time

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Complete)")
st.title("⛽ 燃料明細 自動抽出ツール (決定版)")

# --- CSS: デザイン調整 ---
st.markdown("""
    <style>
    /* ボタンを太字に */
    .stButton button { font-weight: bold; }
    /* 集計表の文字サイズ調整 */
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
# 画面の状態（ズーム、回転、データ、ハイライト）を保存する変数を定義
if 'zoom_level' not in st.session_state: st.session_state['zoom_level'] = 100
if 'rotation' not in st.session_state: st.session_state['rotation'] = 0
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'highlight_text' not in st.session_state: st.session_state['highlight_text'] = []
if 'last_file_id' not in st.session_state: st.session_state['last_file_id'] = None

# --- 関数: PDFを画像化 + マーカー描画 ---
def get_pdf_images(file_bytes, texts_to_highlight=None):
    """
    PDFを画像に変換し、指定されたテキストがあれば赤枠で囲む
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    
    for page in doc:
        # ハイライト処理 (検索して矩形を描画)
        if texts_to_highlight:
            for text in texts_to_highlight:
                # 誤検出防止のため、空文字や「円」などの短い単語は無視できるが、今回はそのまま検索
                if text and len(str(text)) > 0:
                    # テキストを検索 (完全一致ではなく部分一致)
                    quads = page.search_for(str(text))
                    # 赤い枠を描画
                    for quad in quads:
                        # color=(R, G, B) 0~1で指定。赤=(1, 0, 0)
                        page.draw_rect(quad, color=(1, 0, 0), width=4, fill_opacity=0.2, fill=(1, 0.8, 0.8))

        # 画像化 (dpi=150で十分綺麗)
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg"])

# ファイルが変更されたらデータをリセット
if uploaded_file:
    # ファイル名とサイズで同一ファイルか判定
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
    
    # レイアウト: 左(PDFビュアー) vs 右(操作＆結果)
    col1, col2 = st.columns([1.5, 1])

    # --- 左カラム: ビューア ---
    with col1:
        # ツールバー (ズーム・回転)
        c1, c2, c3, c4, c5, _ = st.columns([1,1,1,1,1,5])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}), help="拡大")
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}), help="縮小")
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}), help="右回転")
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}), help="左回転")
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}), help="リセット")

        # 画像生成 (ハイライト付き)
        display_images = []
        if uploaded_file.type == "application/pdf":
            # セッションにある「highlight_text」を渡して、該当箇所を赤く塗る
            display_images = get_pdf_images(file_bytes, st.session_state['highlight_text'])
        else:
            img = Image.open(io.BytesIO(file_bytes))
            display_images = [img]

        # 表示エリア (高さ800px固定・スクロール可能)
        with st.container(height=800):
            # ズーム倍率を反映した幅
            current_width = int(800 * (st.session_state['zoom_level'] / 100))
            
            for img in display_images:
                # 回転処理
                if st.session_state['rotation']:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=current_width)

    # --- 右カラム: 操作 & 結果 ---
    with col2:
        # 抽出ボタン
        if st.button("🚀 抽出実行", type="primary", use_container_width=True):
            try:
                model = genai.GenerativeModel(selected_model_name)
                
                # 解析用画像 (ハイライトなし・回転反映)
                inputs = []
                if uploaded_file.type == "application/pdf":
                    raw_images = get_pdf_images(file_bytes, None) # ハイライトなし
                else:
                    raw_images = [Image.open(io.BytesIO(file_bytes))]
                
                # 回転状態を反映させてからAIに渡す
                for img in raw_images:
                    if st.session_state['rotation']:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                    inputs.append(img)

                prompt = """
                この請求書画像を解析し、以下の情報をJSON形式のみで出力してください。Markdownコードブロックは不要です。
                
                1. **items**: 以下の項目のリスト
                   - 日付 (MM-DD形式)
                   - 燃料名 (レギュラー, 軽油, 軽油税など。軽油税が別行なら必ず抽出)
                   - 使用量 (L) 数値のみ
                   - 請求額 (円) 数値のみ
                   - 明細以外の「合計」行は除外してください。
                2. **tax**: "税込" または "税抜"
                
                出力例:
                {"tax": "税込", "items": [{"日付": "01-15", "燃料名": "ハイオク", "使用量": 45.2, "請求額": 7800}]}
                """
                
                with st.spinner("AIが解析中..."):
                    res = model.generate_content([prompt] + inputs)
                    # JSONのクリーニング
                    text = res.text.replace("```json", "").replace("```", "").strip()
                    if text.startswith("JSON"): text = text[4:]
                    data = json.loads(text)
                    
                    # データフレーム化して保存
                    st.session_state['df'] = pd.DataFrame(data["items"])
                    st.session_state['tax_type'] = data.get("tax", "不明")
                    st.session_state['highlight_text'] = [] # ハイライトリセット
                    
                    st.toast("抽出が完了しました！", icon="✅")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
                st.info("※「429」エラーの場合はAPI制限です。数分待ってから再試行してください。")

        # --- 結果表示 & 編集エリア ---
        # データがある場合のみ表示
        if not st.session_state['df'].empty:
            df = st.session_state['df']
            
            # 数値型へ変換 (計算のため)
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

            st.markdown(f"**💰 消費税区分:** `{st.session_state.get('tax_type')}`")

            # ---------------------------
            # 1. 集計サマリ表 (常に自動計算)
            # ---------------------------
            st.markdown("##### 📊 集計サマリ")
            
            # 燃料ごとの集計
            summary_df = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
            # 総合計行を作成
            total_usage = summary_df["使用量"].sum()
            total_cost = summary_df["請求額"].sum()
            
            # 合計行をデータフレームに追加
            total_row = pd.DataFrame({
                "燃料名": ["🔴 合計"],
                "使用量": [total_usage],
                "請求額": [total_cost]
            })
            summary_display = pd.concat([summary_df, total_row], ignore_index=True)

            # サマリ表示
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
            st.markdown("##### 📝 詳細データ (編集・行追加・クリックでPDF検索)")
            st.caption("行をクリックすると、PDF内の該当箇所を赤枠で表示します。下の「＋」で行追加可能。")

            # ---------------------------
            # 2. 詳細データ編集エディタ
            # ---------------------------
            edited_df = st.data_editor(
                df,
                num_rows="dynamic",     # 行の追加・削除を許可
                use_container_width=True,
                hide_index=True,
                key="editor",           # 選択状態を取得するためのキー
                on_change=None,         
                selection_mode="single-row", # 行選択モード (v1.35以上必須)
                column_config={
                    "日付": st.column_config.TextColumn(),
                    "燃料名": st.column_config.TextColumn(),
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )

            # ---------------------------
            # 3. 変更検知ロジック
            # ---------------------------
            
            # A. データの中身が変わった場合 (数値修正や行追加)
            # 比較して変更があればセッションを更新してリロード(合計表を更新するため)
            if not edited_df.equals(st.session_state['df']):
                st.session_state['df'] = edited_df
                st.rerun() 

            # B. 行選択が変わった場合 (PDFハイライト機能)
            # エディタの選択状態を取得
            if "editor" in st.session_state and st.session_state.editor.get("selection"):
                selection = st.session_state.editor["selection"]
                if selection.get("rows"):
                    row_idx = selection["rows"][0]
                    # 有効な行かチェック
                    if row_idx < len(edited_df):
                        selected_row = edited_df.iloc[row_idx]
                        
                        # PDF検索用のキーワードを作成
                        # 日付、金額(整数化)、燃料名をリストにする
                        targets = [
                            str(selected_row["日付"]),
                            str(int(selected_row["請求額"])), 
                            str(selected_row["燃料名"])
                        ]
                        
                        # ハイライト対象が変わったら更新してリロード
                        if st.session_state['highlight_text'] != targets:
                            st.session_state['highlight_text'] = targets
                            st.rerun()
            else:
                # 選択が外れたらハイライトも消す
                if st.session_state['highlight_text']:
                    st.session_state['highlight_text'] = []
                    st.rerun()

            # CSVダウンロードボタン
            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="CSVダウンロード",
                data=csv,
                file_name="fuel_data.csv",
                mime="text/csv",
                use_container_width=True
            )
