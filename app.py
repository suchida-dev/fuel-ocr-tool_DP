import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
import os
import time

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="その他燃料明細OCR")
st.title("⛽ その他燃料明細 自動抽出ツール")

# --- 1. APIキー設定 ---
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("✅ 認証済み (共有キーを使用)")
else:
    api_key_input = st.sidebar.text_input("Gemini API Key", type="password")
    api_key = api_key_input.strip() if api_key_input else None

# --- 2. モデルの動的取得 ---
available_model_names = []
if api_key:
    # 接続安定化のため REST を指定
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
    selected_model_name = st.sidebar.selectbox(
        "使用するモデル", 
        available_model_names
    )

# --- 3. ファイルアップロード (複数対応) ---
# accept_multiple_files=True に変更しました
uploaded_files = st.file_uploader(
    "請求書(PDF/画像)をアップロード（複数選択可）", 
    type=["pdf", "png", "jpg", "jpeg"], 
    accept_multiple_files=True
)

def pdf_page_to_image(pdf_file):
    # ストリーム位置をリセット（念のため）
    pdf_file.seek(0)
    doc = fitz.open(stream=pdf_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap()
    img_data = pix.tobytes("png")
    return Image.open(io.BytesIO(img_data))

# --- メイン処理 ---
if uploaded_files and api_key and selected_model_name:
    
    st.markdown(f"**📂 {len(uploaded_files)} 件のファイルが選択されています**")
    
    if st.button("一括抽出を開始する", type="primary"):
        
        all_results = [] # 全データ格納用リスト
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []

        # モデルの準備
        model = genai.GenerativeModel(selected_model_name)
        
        # プロンプト（共通）
        prompt = """
        このガソリンスタンドの請求書画像を解析してください。
        以下の情報を抽出し、JSON形式で出力してください。Markdownは不要です。

        1. **明細リスト**: 日付、燃料名、使用量(L)、請求額(円)
           - 明細以外の「合計」行は除外。
           - 軽油税が別行ならそれも抽出。
        2. **税区分**: "税込" または "税抜"

        出力JSONフォーマット:
        {
            "tax_type": "税込" または "税抜",
            "items": [
                {
                    "日付": "MM-DD",
                    "燃料名": "燃料の種類",
                    "使用量": 数値(0 if none),
                    "請求額": 数値
                }
            ]
        }
        数値にはカンマや円マークを入れないでください。
        """

        # ループ処理
        for i, file in enumerate(uploaded_files):
            status_text.text(f"⏳ 処理中 ({i+1}/{len(uploaded_files)}): {file.name} ...")
            
            try:
                # 画像化
                if file.type == "application/pdf":
                    image = pdf_page_to_image(file)
                else:
                    image = Image.open(file)

                # Geminiへ送信
                response = model.generate_content([prompt, image])
                
                # JSONパース
                json_text = response.text.replace("```json", "").replace("```", "").strip()
                if json_text.startswith("JSON"): json_text = json_text[4:]
                
                data = json.loads(json_text)
                
                # ファイル名と税区分を各行に追加してリストへ
                tax_type = data.get("tax_type", "不明")
                for item in data.get("items", []):
                    item["ファイル名"] = file.name
                    item["税区分"] = tax_type
                    all_results.append(item)
                    
            except Exception as e:
                error_log.append(f"{file.name}: {e}")
                continue # エラーでも止まらず次へ

            # 進捗更新
            progress_bar.progress((i + 1) / len(uploaded_files))
            time.sleep(1) # API制限回避のための安全待機（1秒）

        status_text.success("✅ 全ファイルの処理が完了しました！")
        
        # 結果の保存
        if all_results:
            df = pd.DataFrame(all_results)
            # 列の並び順を整理
            cols = ["ファイル名", "日付", "燃料名", "使用量", "請求額", "税区分"]
            # 存在しない列があれば除外して並べ替え
            df = df[[c for c in cols if c in df.columns]]
            st.session_state['batch_df'] = df
        else:
            st.warning("データが抽出できませんでした。")
        
        if error_log:
            with st.expander("⚠️ エラーが発生したファイル"):
                for err in error_log:
                    st.write(err)

    # --- 結果表示エリア ---
    if 'batch_df' in st.session_state:
        df = st.session_state['batch_df']
        
        # 数値変換
        df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
        df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)

        # 1. 総合計の表示
        total_usage = df["使用量"].sum()
        total_cost = df["請求額"].sum()
        
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("処理ファイル数", f"{df['ファイル名'].nunique()} 件")
        col_m2.metric("合計使用量", f"{total_usage:,.2f} L")
        col_m3.metric("合計請求額", f"¥{total_cost:,.0f}")
        
        st.markdown("---")

        # 2. 燃料別の集計（全ファイル合計）
        st.markdown("##### ⛽ 燃料別・全社合計")
        grouped = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
        st.dataframe(grouped, hide_index=True, use_container_width=True)

        st.markdown("---")
        
        # 3. 詳細データの編集
        st.markdown("##### 📝 詳細データリスト")
        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "請求額": st.column_config.NumberColumn(format="¥%d"),
                "使用量": st.column_config.NumberColumn(format="%.2f L"),
            }
        )
        
        # 4. ダウンロード
        csv = edited_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="全データをCSVでダウンロード",
            data=csv,
            file_name="fuel_data_batch.csv",
            mime="text/csv",
            type="primary"
        )
