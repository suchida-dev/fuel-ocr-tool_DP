import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
from streamlit_pdf_viewer import pdf_viewer  # 【追加】専用ライブラリ

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (PDFビューア版)")
st.title("⛽ 燃料明細 自動抽出ツール")

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

# --- 3. ファイルアップロード ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg", "jpeg"])

# --- 関数: 解析用にPDFを画像リストに変換 ---
def pdf_to_all_images(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap()
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- メイン処理 ---
if uploaded_file and api_key and selected_model_name:
    
    # ファイル読み込み
    file_bytes = uploaded_file.read()
    input_contents = [] 
    
    if uploaded_file.type == "application/pdf":
        input_contents = pdf_to_all_images(file_bytes)
    else:
        image = Image.open(io.BytesIO(file_bytes))
        input_contents = [image]

    # --- 画面構成 ---
    col1, col2 = st.columns([1.5, 1])

    with col1:
        st.subheader("📄 原本プレビュー")
        # 【修正】ここを専用ライブラリに変更しました
        if uploaded_file.type == "application/pdf":
            # widthは親カラムに合わせて自動調整、高さはスクロール可能
            pdf_viewer(input=file_bytes, width=700, height=800)
        else:
            st.image(input_contents[0], use_container_width=True)

    with col2:
        st.subheader("📊 抽出結果")
        
        if st.button("抽出を開始する", type="primary"):
            st.info(f"使用モデル: {selected_model_name} / 処理ページ数: {len(input_contents)}枚")
            
            try:
                model = genai.GenerativeModel(selected_model_name)
                
                prompt = """
                このガソリンスタンドの請求書（全ページ）を解析してください。
                以下の情報を抽出し、JSON形式で出力してください。Markdownは不要です。

                1. **明細リスト**: 日付、燃料名、使用量(L)、請求額(円)
                   - ページをまたいでいる場合もすべて抽出。
                   - 軽油税が別行ならそれも明細として抽出。
                   - 明細以外の「合計」行は除外。
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
                数値にはカンマや円マークを入れないでください。
                """
                
                request_content = [prompt] + input_contents

                with st.spinner("全ページ解析中..."):
                    response = model.generate_content(request_content)
                
                json_text = response.text.replace("```json", "").replace("```", "").strip()
                if json_text.startswith("JSON"): json_text = json_text[4:]
                
                full_data = json.loads(json_text)
                df = pd.DataFrame(full_data["items"])
                
                st.session_state['df'] = df
                st.session_state['tax_type'] = full_data.get("tax_type", "不明")
                st.success("成功しました！")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

        # 結果表示
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
                    st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv")

                except Exception as e:
                    st.error(f"データ処理中にエラー: {e}")
