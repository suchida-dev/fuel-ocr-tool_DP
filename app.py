import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
import os

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (共有版)")
st.title("⛽ 燃料明細 自動抽出ツール")

# --- 1. APIキー設定 (Secrets対応版) ---
api_key = None

# A. Streamlit Cloudの「Secrets」にキーが設定されている場合
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("✅ 認証済み (共有キーを使用)")
# B. 設定がない場合 (ローカルテスト用など)
else:
    api_key_input = st.sidebar.text_input("Gemini API Key", type="password")
    api_key = api_key_input.strip() if api_key_input else None

# --- 2. モデルの動的取得 ---
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

# モデル選択 (リストがある場合のみ表示)
selected_model_name = None
if available_model_names:
    selected_model_name = st.sidebar.selectbox(
        "使用するモデル", 
        available_model_names
    )

# --- 3. ファイルアップロード ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg", "jpeg"])

def pdf_page_to_image(pdf_file):
    doc = fitz.open(stream=pdf_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap()
    img_data = pix.tobytes("png")
    return Image.open(io.BytesIO(img_data))

# --- メイン処理 ---
if uploaded_file and api_key and selected_model_name:
    if uploaded_file.type == "application/pdf":
        image = pdf_page_to_image(uploaded_file)
    else:
        image = Image.open(uploaded_file)

    col1, col2 = st.columns([1.5, 1])

    with col1:
        st.subheader("📄 原本")
        st.image(image, use_container_width=True)

    with col2:
        st.subheader("📊 抽出結果")
        
        if st.button("抽出を開始する", type="primary"):
            st.info(f"使用モデル: {selected_model_name}")
            
            try:
                model = genai.GenerativeModel(selected_model_name)
                
                prompt = """
                このガソリンスタンドの請求書画像を解析してください。
                以下の3つの情報を抽出し、必ず指定のJSON形式で出力してください。
                Markdownコードブロックは不要です。生JSONのみ返してください。

                1. **明細リスト**: 日付、燃料名、使用量(L)、請求額(円)
                   - 「軽油税」が個別の行として記載されている場合は、それも明細行として抽出すること。
                   - 明細以外の「合計」行は除外すること。
                2. **税区分**: 書類全体を見て、金額が「税込」か「税抜」か判定すること。
                3. **メタ情報**: その他気づいたことがあれば記述。

                出力JSONフォーマット:
                {
                    "tax_type": "税込" または "税抜",
                    "items": [
                        {
                            "日付": "MM-DD",
                            "燃料名": "レギュラー、軽油、軽油税など",
                            "使用量": 数値(数値がない場合は 0),
                            "請求額": 数値
                        }
                    ]
                }
                数値にはカンマや円マークを入れないでください。
                """

                with st.spinner("解析中..."):
                    response = model.generate_content([prompt, image])
                
                json_text = response.text.replace("```json", "").replace("```", "").strip()
                if json_text.startswith("JSON"): json_text = json_text[4:]
                
                full_data = json.loads(json_text)
                df = pd.DataFrame(full_data["items"])
                
                st.session_state['df'] = df
                st.session_state['tax_type'] = full_data.get("tax_type", "不明")
                st.success("成功しました！")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

        if 'df' in st.session_state:
            df = st.session_state['df']
            tax_type = st.session_state.get('tax_type', '不明')

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
                df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )
            
            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv")