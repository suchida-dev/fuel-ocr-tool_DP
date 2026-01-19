import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
import re

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Base)")
st.title("⛽ 燃料明細 自動抽出ツール")
st.caption("状態: 安定版 (リセット完了)")

# --- CSS ---
st.markdown("""
    <style>
    .stButton button { font-weight: bold; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    </style>
""", unsafe_allow_html=True)

# --- 1. APIキー ---
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("✅ 認証済み")
else:
    api_key_input = st.sidebar.text_input("Gemini API Key", type="password")
    api_key = api_key_input.strip() if api_key_input else None

# --- 2. モデル ---
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
    selected_model_name = st.sidebar.selectbox("使用モデル", available_model_names, index=0)

# --- 3. セッション初期化 ---
if 'zoom_level' not in st.session_state: st.session_state['zoom_level'] = 100
if 'rotation' not in st.session_state: st.session_state['rotation'] = 0
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'last_file_id' not in st.session_state: st.session_state['last_file_id'] = None

# --- 関数: PDF画像化 (マーカー機能なしのシンプル版) ---
def get_pdf_images(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- 関数: JSON抽出 (修復機能付き) ---
def extract_json(text):
    try:
        return json.loads(text)
    except:
        pass
    try:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1:
            json_str = text[start:end]
            return json.loads(json_str)
    except:
        pass
    return None

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg"])

# ファイル変更時にリセット
if uploaded_file:
    file_id = uploaded_file.name + str(uploaded_file.size)
    if st.session_state['last_file_id'] != file_id:
        st.session_state['last_file_id'] = file_id
        st.session_state['df'] = pd.DataFrame()
        st.session_state['tax_type'] = "ー"
        st.session_state['zoom_level'] = 100
        st.session_state['rotation'] = 0

if uploaded_file and api_key and selected_model_name:
    file_bytes = uploaded_file.read()
    col1, col2 = st.columns([1.5, 1])

    # --- 左: ビューア ---
    with col1:
        c1, c2, c3, c4, c5, _ = st.columns([1,1,1,1,1,5])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}), help="拡大")
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}), help="縮小")
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}), help="右回転")
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}), help="左回転")
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}), help="リセット")

        display_images = []
        if uploaded_file.type == "application/pdf":
            display_images = get_pdf_images(file_bytes)
        else:
            img = Image.open(io.BytesIO(file_bytes))
            display_images = [img]

        with st.container(height=800):
            current_width = int(800 * (st.session_state['zoom_level'] / 100))
            for img in display_images:
                if st.session_state['rotation']:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=current_width)

    # --- 右: 操作 ---
    with col2:
        if st.button("🚀 抽出実行", type="primary", use_container_width=True):
            try:
                model = genai.GenerativeModel(selected_model_name)
                inputs = []
                for img in display_images:
                     if st.session_state['rotation']:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                     inputs.append(img)

                prompt = """
                請求書画像を解析し、以下の情報をJSON形式のみで出力してください。Markdown不要。
                
                1. **items**: 以下のリスト
                   - 日付 (MM-DD)
                   - 燃料名 (ガソリン, 軽油, 灯油, 重油, 軽油税などCO2排出対象のみ。洗車等は除外)
                   - 使用量 (L) 数値
                   - 請求額 (円) 数値
                2. **tax**: "税込" or "税抜"
                """
                
                with st.spinner("解析中..."):
                    res = model.generate_content([prompt] + inputs)
                    data = extract_json(res.text)
                    
                    if data:
                        # --- 強制カラムチェック (KeyError防止) ---
                        df_new = pd.DataFrame(data.get("items", []))
                        required_cols = ["日付", "燃料名", "使用量", "請求額"]
                        
                        if df_new.empty:
                            df_new = pd.DataFrame(columns=required_cols)
                        
                        for col in required_cols:
                            if col not in df_new.columns:
                                df_new[col] = 0 if col in ["使用量", "請求額"] else ""
                                    
                        st.session_state['df'] = df_new
                        st.session_state['tax_type'] = data.get("tax", "不明")
                        st.toast("抽出完了", icon="✅")
                    else:
                        st.error("解析失敗: データ形式が読み取れませんでした。")

            except Exception as e:
                st.error(f"エラー: {e}")

        # --- 結果表示 ---
        if not st.session_state['df'].empty:
            df = st.session_state['df']
            
            # 数値変換
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)
            df["日付"] = df["日付"].astype(str)
            df["燃料名"] = df["燃料名"].astype(str)

            st.markdown(f"**💰 消費税:** `{st.session_state.get('tax_type')}`")

            # 集計
            st.markdown("##### 📊 集計サマリ")
            summary_df = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
            total_row = pd.DataFrame({
                "燃料名": ["🔴 合計"],
                "使用量": [summary_df["使用量"].sum()],
                "請求額": [summary_df["請求額"].sum()]
            })
            st.dataframe(
                pd.concat([summary_df, total_row], ignore_index=True),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )

            st.markdown("---")
            st.markdown("##### 📝 詳細データ")

            # --- 安定版エディタ ---
            # キーを "editor_reset" に変更してキャッシュをクリア
            # selection_mode を削除し、dynamic（行追加可能）を優先
            edited_df = st.data_editor(
                df,
                num_rows="dynamic", # 行追加・削除OK
                use_container_width=True,
                hide_index=True,
                key="editor_reset", 
                column_config={
                    "日付": st.column_config.TextColumn(),
                    "燃料名": st.column_config.TextColumn(),
                    "請求額": st.column_config.NumberColumn(format="¥%d"),
                    "使用量": st.column_config.NumberColumn(format="%.2f L"),
                }
            )
            
            if not edited_df.equals(st.session_state['df']):
                st.session_state['df'] = edited_df
                st.rerun() 
            
            # CSVダウンロード
            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)
