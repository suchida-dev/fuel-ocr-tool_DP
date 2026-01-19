import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Full View)")
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
    st.sidebar.success("✅ 認証済み (共有キーを使用)")
else:
    api_key_input = st.sidebar.text_input("Gemini API Key", type="password")
    api_key = api_key_input.strip() if api_key_input else None

# --- 2. モデル取得 ---
available_model_names = []
if api_key:
    genai.configure(api_key=api_key, transport='rest')
    try:
        with st.spinner("利用可能なモデルを問い合わせ中..."):
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    available_model_names.append(m.name)
        if not available_model_names:
            st.sidebar.error("❌ 利用可能なモデルが見つかりませんでした。")
    except Exception as e:
        st.sidebar.error(f"モデル一覧の取得に失敗: {e}")

selected_model_name = None
if available_model_names:
    selected_model_name = st.sidebar.selectbox("使用するモデル", available_model_names)

# --- 3. セッション状態の初期化 ---
if 'zoom_level' not in st.session_state:
    st.session_state['zoom_level'] = 100 # %単位
if 'rotation' not in st.session_state:
    st.session_state['rotation'] = 0
if 'last_uploaded_file' not in st.session_state:
    st.session_state['last_uploaded_file'] = None

# --- 関数 ---
def pdf_to_all_images(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg", "jpeg"])

# ファイルが変更されたらデータをリセットする処理
if uploaded_file:
    # ファイル固有のID（あるいは名前）で判定
    file_id = uploaded_file.name + str(uploaded_file.size)
    if st.session_state['last_uploaded_file'] != file_id:
        st.session_state['last_uploaded_file'] = file_id
        # 以前の結果をクリア
        if 'df' in st.session_state: del st.session_state['df']
        if 'tax_type' in st.session_state: del st.session_state['tax_type']
        # ビューアもリセット
        st.session_state['zoom_level'] = 100
        st.session_state['rotation'] = 0

if uploaded_file and api_key and selected_model_name:
    
    file_bytes = uploaded_file.read()
    input_contents = [] 
    
    if uploaded_file.type == "application/pdf":
        input_contents = pdf_to_all_images(file_bytes)
    else:
        image = Image.open(io.BytesIO(file_bytes))
        input_contents = [image]

    # --- 画面構成: 左側(ビュアー)を大きく取る [2:1] ---
    col1, col2 = st.columns([2, 1])

    with col1:
        # --- コントロールバー ---
        c1, c2, c3, c4, c5, c_spacer = st.columns([1, 1, 1, 1, 1, 6])
        
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

        # --- 画像表示エリア (固定高さ850px) ---
        with st.container(height=850):
            # ズーム倍率に応じた幅を計算 (基準幅を大きく1000pxに設定)
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
                
                # AIに見せる画像（現在の回転を反映）を作成
                processed_inputs = []
                for img in input_contents:
                    if st.session_state['rotation'] != 0:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                    processed_inputs.append(img)
                
                prompt = """
                この請求書画像を解析してください。
                以下の情報を抽出し、JSON形式で出力してください。Markdownは不要です。

                1. **明細リスト**: 日付、燃料名、使用量(L)、請求額(円)
                   - ページをまたいでいる場合もすべて抽出。
                   - 明細以外の「合計」行は除外。
                   - 軽油税が別行なら抽出。
                2. **税区分**: "税込" または "税抜"
                
                出力JSONフォーマット:
                {
                    "tax_type": "税込" または "税抜",
                    "items": [
                        {
                            "日付": "MM-DD",
                            "燃料名": "名称",
                            "使用量": 数値,
                            "請求額": 数値
                        }
                    ]
                }
                """
                
                request_content = [prompt] + processed_inputs

                with st.spinner("解析中..."):
                    response = model.generate_content(request_content)
                
                json_text = response.text.replace("```json", "").replace("```", "").strip()
                if json_text.startswith("JSON"): json_text = json_text[4:]
                
                full_data = json.loads(json_text)
                df = pd.DataFrame(full_data["items"])
                
                # 結果を保存
                st.session_state['df'] = df
                st.session_state['tax_type'] = full_data.get("tax_type", "不明")
                
                # 成功メッセージ（一時的ではなく、ずっと残るようにコンテナ外で表示）
                st.toast("抽出が完了しました！", icon="✅")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

        # --- 結果の常時表示エリア ---
        # セッションステートにデータがある限り、回転やズームをしてもここが表示され続ける
        if 'df' in st.session_state:
            df = st.session_state['df']
            tax_type = st.session_state.get('tax_type', '不明')

            required_cols = ["使用量", "請求額", "燃料名"]
            missing_cols = [c for c in required_cols if c not in df.columns]

            if missing_cols:
                st.error("電気もしくはガスのデータです。データを再確認してください。")
            else:
                try:
                    df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
                    df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

                    st.markdown(f"**💰 消費税区分:** `{tax_type}`")
                    st.markdown("##### ⛽ 燃料別合計")
                    
                    grouped = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
                    for index, row in grouped.iterrows():
                        usage_str = f"{row['使用量']:.2f} L" if row['使用量'] > 0 else "-"
                        st.info(f"**{row['燃料名']}**: {usage_str} / ¥{row['請求額']:,.0f}")

                    st.markdown("---")

                    edited_df = st.data_editor(
                        df, num_rows="dynamic", use_container_width=True,
                        column_config={
                            "請求額": st.column_config.NumberColumn(format="¥%d"),
                            "使用量": st.column_config.NumberColumn(format="%.2f L"),
                        }
                    )
                    
                    csv = edited_df.to_csv(index=False).encode('utf-8-sig')
                    st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)

                except Exception as e:
                    st.error(f"データ表示エラー: {e}")
