import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Clean Columns)")
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
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_model_names.append(m.name)
    except: pass

selected_model_name = None
if available_model_names:
    selected_model_name = st.sidebar.selectbox("使用するモデル", available_model_names)

# --- 3. セッション初期化 ---
if 'zoom_level' not in st.session_state: st.session_state['zoom_level'] = 100
if 'rotation' not in st.session_state: st.session_state['rotation'] = 0
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'last_file_id' not in st.session_state: st.session_state['last_file_id'] = None

# --- 関数: シンプルなPDF画像化 ---
def pdf_to_all_images(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- 関数: JSON抽出 ---
def extract_json(text):
    try:
        return json.loads(text)
    except:
        pass
    try:
        s = text.find('{')
        e = text.rfind('}') + 1
        return json.loads(text[s:e])
    except:
        return None

# --- 関数: データの強制整形 (列が増えるのを防ぐ) ---
def clean_data_items(items):
    """AIが返すキーの揺らぎ（使用量(L), 金額など）を吸収して4列に統一する"""
    cleaned_list = []
    for item in items:
        new_row = {
            "日付": "",
            "燃料名": "",
            "使用量": 0,
            "請求額": 0
        }
        # 辞書のキーを見て、適切な列に割り振る
        for k, v in item.items():
            key_str = str(k)
            # 日付系
            if any(x in key_str for x in ["日付", "Date", "date", "day"]):
                new_row["日付"] = v
            # 燃料名系
            elif any(x in key_str for x in ["燃料", "品名", "商品", "name", "Name", "item"]):
                new_row["燃料名"] = v
            # 使用量系 (L, 数量, amountなど)
            elif any(x in key_str for x in ["使用量", "数量", "L", "amount", "vol"]):
                try:
                    new_row["使用量"] = float(str(v).replace(",", ""))
                except:
                    new_row["使用量"] = 0
            # 金額系 (円, 請求額, priceなど)
            elif any(x in key_str for x in ["請求額", "金額", "価格", "price", "cost", "円"]):
                try:
                    new_row["請求額"] = float(str(v).replace(",", ""))
                except:
                    new_row["請求額"] = 0
        
        cleaned_list.append(new_row)
    return cleaned_list

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file:
    file_id = uploaded_file.name + str(uploaded_file.size)
    if st.session_state['last_file_id'] != file_id:
        st.session_state['last_file_id'] = file_id
        st.session_state['df'] = pd.DataFrame()
        if 'tax_type' in st.session_state: del st.session_state['tax_type']
        st.session_state['zoom_level'] = 100
        st.session_state['rotation'] = 0

if uploaded_file and api_key and selected_model_name:
    file_bytes = uploaded_file.read()
    
    # 画像生成
    input_contents = []
    if uploaded_file.type == "application/pdf":
        input_contents = pdf_to_all_images(file_bytes)
    else:
        input_contents = [Image.open(io.BytesIO(file_bytes))]

    col1, col2 = st.columns([2, 1])

    # --- 左: ビューア ---
    with col1:
        c1, c2, c3, c4, c5, _ = st.columns([1, 1, 1, 1, 1, 6])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}))
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}))
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}))
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}))
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}))

        with st.container(height=850):
            current_width = int(1000 * (st.session_state['zoom_level'] / 100))
            for img in input_contents:
                if st.session_state['rotation'] != 0:
                    img = img.rotate(st.session_state['rotation'], expand=True)
                st.image(img, width=current_width)

    # --- 右: 操作と表 ---
    with col2:
        st.subheader("📊 抽出結果")
        
        if st.button("抽出を開始する", type="primary", use_container_width=True):
            st.info(f"処理ページ数: {len(input_contents)}枚")
            
            try:
                model = genai.GenerativeModel(selected_model_name)
                
                # 画像準備
                processed_inputs = []
                for img in input_contents:
                    if st.session_state['rotation'] != 0:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                    processed_inputs.append(img)
                
                prompt = """
                請求書画像を解析し、JSON形式で出力してください。Markdownは不要。
                
                1. **items**: 明細リスト (日付, 燃料名, 使用量(L), 請求額(円))
                   - 合計行は除外。
                   - キー名は必ず "日付", "燃料名", "使用量", "請求額" に統一すること。
                2. **tax_type**: "税込" または "税抜"
                """
                
                with st.spinner("解析中..."):
                    res = model.generate_content([prompt] + processed_inputs)
                    full_data = extract_json(res.text)
                    
                    if full_data:
                        raw_items = full_data.get("items", [])
                        # ここで強力に列を統一する
                        cleaned_items = clean_data_items(raw_items)
                        
                        df = pd.DataFrame(cleaned_items)
                        
                        # DataFrameが空の場合でも、必ず4列を作る
                        required_columns = ["日付", "燃料名", "使用量", "請求額"]
                        if df.empty:
                            df = pd.DataFrame(columns=required_columns)
                        else:
                            # 必要な列だけに絞り込む (余計な列を削除)
                            df = df[required_columns]

                        st.session_state['df'] = df
                        st.session_state['tax_type'] = full_data.get("tax_type", "不明")
                        st.toast("完了", icon="✅")
                    else:
                        st.error("解析失敗")

            except Exception as e:
                st.error(f"エラー: {e}")

        # --- 表の表示 ---
        if 'df' in st.session_state and not st.session_state['df'].empty:
            df = st.session_state['df']
            
            # 数値変換 (念のため再変換)
            df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
            df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)
            df["日付"] = df["日付"].astype(str).replace("nan", "")
            df["燃料名"] = df["燃料名"].astype(str).replace("nan", "")

            st.markdown(f"**💰 消費税:** `{st.session_state.get('tax_type', '不明')}`")
            
            # 合計表示
            grouped = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
            for _, row in grouped.iterrows():
                usage = f"{row['使用量']:.2f} L" if row['使用量'] > 0 else "-"
                st.info(f"**{row['燃料名']}**: {usage} / ¥{row['請求額']:,.0f}")
            
            st.markdown("---")

            # エディタ設定 (行追加可能)
            edited_df = st.data_editor(
                df,
                num_rows="dynamic", 
                use_container_width=True,
                hide_index=True,
                key="editor_fixed_columns", # キーを一新
                column_config={
                    "日付": st.column_config.TextColumn("日付"),
                    "燃料名": st.column_config.TextColumn("燃料名"),
                    "請求額": st.column_config.NumberColumn("請求額(円)", format="¥%d"),
                    "使用量": st.column_config.NumberColumn("使用量(L)", format="%.2f L"),
                }
            )
            
            if not edited_df.equals(st.session_state['df']):
                st.session_state['df'] = edited_df
                st.rerun() 
            
            csv = edited_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)
