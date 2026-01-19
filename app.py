import streamlit as st
import google.generativeai as genai
from PIL import Image
import pandas as pd
import json
import io
import fitz  # PyMuPDF
import re

# --- 追加ライブラリの読み込み check ---
try:
    from streamlit_drawable_canvas import st_canvas
    HAS_CANVAS = True
except ImportError:
    HAS_CANVAS = False

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="燃料明細OCR (Safe Mode)")
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
    # 優先順位: 3-flash > 2.5-flash > その他
    default_index = 0
    for i, name in enumerate(available_model_names):
        if "gemini-3-flash" in name:
            default_index = i
            break
        elif "gemini-2.5-flash-preview" in name:
            default_index = i
    
    selected_model_name = st.sidebar.selectbox(
        "使用するモデル", 
        available_model_names, 
        index=default_index
    )

# --- 3. セッション初期化 ---
if 'zoom_level' not in st.session_state: st.session_state['zoom_level'] = 100
if 'rotation' not in st.session_state: st.session_state['rotation'] = 0
if 'df' not in st.session_state: st.session_state['df'] = pd.DataFrame()
if 'last_file_id' not in st.session_state: st.session_state['last_file_id'] = None

# --- 関数: PDF画像化 ---
def pdf_to_all_images(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img_data = pix.tobytes("png")
        images.append(Image.open(io.BytesIO(img_data)))
    return images

# --- 関数: JSON抽出 (強化版) ---
def extract_json(text):
    """
    AIの回答からJSONブロックを抽出する。
    JSONDecodeErrorが起きないよう、正規表現で範囲を特定する。
    """
    try:
        # まずは単純にパースを試みる
        return json.loads(text)
    except:
        pass
    
    try:
        # コードブロック ```json ... ``` を探す
        match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        
        # 単純な { ... } を探す (最初と最後)
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1:
            return json.loads(text[start:end])
    except:
        pass
    return None

# --- 関数: データ整形 & 強制フィルタリング ---
def clean_and_normalize_data(items):
    cleaned_list = []
    # 除外キーワード
    exclude_keywords = [
        "電気", "ガス", "基本料金", "水道", 
        "オイル", "交換", "工賃", "タイヤ", "バッテリー", 
        "エレメント", "洗車", "部品", "ワイパー", "AdBlue"
    ]

    for item in items:
        # 初期値で埋める
        new_row = {"日付": "", "燃料名": "", "使用量": 0, "請求額": 0}
        
        # 列のマッピング
        for k, v in item.items():
            key_str = str(k)
            val_str = str(v)
            if any(x in key_str for x in ["日付", "Date", "date"]):
                new_row["日付"] = val_str
            elif any(x in key_str for x in ["燃料", "品名", "商品", "name"]):
                new_row["燃料名"] = val_str
            elif any(x in key_str for x in ["使用量", "数量", "L", "amount"]):
                try: new_row["使用量"] = float(str(val_str).replace(",", ""))
                except: new_row["使用量"] = 0
            elif any(x in key_str for x in ["請求額", "金額", "price", "円"]):
                try: new_row["請求額"] = float(str(val_str).replace(",", ""))
                except: new_row["請求額"] = 0
        
        # フィルタリング
        fuel_name = str(new_row["燃料名"])
        is_gasoline = "ガソリン" in fuel_name
        should_exclude = False
        for kw in exclude_keywords:
            if kw in fuel_name:
                if kw == "ガス" and is_gasoline: continue
                should_exclude = True
                break
        
        if not should_exclude:
            cleaned_list.append(new_row)
    return cleaned_list

# --- メイン処理 ---
uploaded_file = st.file_uploader("請求書(PDF/画像)をアップロード", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file:
    file_id = uploaded_file.name + str(uploaded_file.size)
    if st.session_state['last_file_id'] != file_id:
        st.session_state['last_file_id'] = file_id
        st.session_state['df'] = pd.DataFrame() # リセット
        if 'tax_type' in st.session_state: del st.session_state['tax_type']
        st.session_state['zoom_level'] = 100
        st.session_state['rotation'] = 0

if uploaded_file and api_key and selected_model_name:
    file_bytes = uploaded_file.read()
    
    input_contents = []
    if uploaded_file.type == "application/pdf":
        input_contents = pdf_to_all_images(file_bytes)
    else:
        input_contents = [Image.open(io.BytesIO(file_bytes))]

    col1, col2 = st.columns([2, 1])

    # --- 左: ビューア (安全装置付きCanvas) ---
    with col1:
        # コントロール
        c1, c2, c3, c4, c5, c_toggle = st.columns([1, 1, 1, 1, 1, 3])
        with c1: st.button("➕", on_click=lambda: st.session_state.update({'zoom_level': st.session_state['zoom_level']+25}))
        with c2: st.button("➖", on_click=lambda: st.session_state.update({'zoom_level': max(10, st.session_state['zoom_level']-25)}))
        with c3: st.button("⤵", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']-90)%360}))
        with c4: st.button("⤴", on_click=lambda: st.session_state.update({'rotation': (st.session_state['rotation']+90)%360}))
        with c5: st.button("R", on_click=lambda: st.session_state.update({'zoom_level': 100, 'rotation': 0}))
        
        use_canvas = False
        if HAS_CANVAS:
            with c_toggle:
                use_canvas = st.toggle("✏️ 手書きモード", value=False)
        else:
            with c_toggle:
                st.caption("※描画機能なし")

        # ペン設定
        stroke_color = "rgba(255, 255, 0, 0.4)"
        stroke_width = 20
        if use_canvas:
            pen_col1, pen_col2 = st.columns(2)
            with pen_col1:
                pen_type = st.radio("ペンの種類", ["蛍光マーカー (黄)", "赤ペン"], horizontal=True, label_visibility="collapsed")
                if pen_type == "赤ペン":
                    stroke_color = "rgba(255, 0, 0, 0.8)"
                    stroke_width = 3

        with st.container(height=850):
            current_zoom = st.session_state['zoom_level']
            current_rot = st.session_state['rotation']
            
            base_width = 1000
            display_width = int(base_width * (current_zoom / 100))
            
            for i, img in enumerate(input_contents):
                if current_rot != 0:
                    img = img.rotate(current_rot, expand=True)
                
                # Canvas表示 (エラーが出たら通常表示にフォールバック)
                if use_canvas:
                    try:
                        aspect_ratio = img.height / img.width
                        display_height = int(display_width * aspect_ratio)
                        resized_img = img.resize((display_width, display_height))
                        
                        canvas_key = f"cv_{file_id}_{i}_{current_zoom}_{current_rot}"
                        
                        st_canvas(
                            fill_color="rgba(0, 0, 0, 0)",
                            stroke_width=stroke_width,
                            stroke_color=stroke_color,
                            background_image=resized_img, # PIL画像をそのまま渡す
                            update_streamlit=True,
                            height=display_height,
                            width=display_width,
                            drawing_mode="freedraw",
                            key=canvas_key,
                        )
                    except Exception as e:
                        # もしCanvasでエラーが出たら警告を出して通常画像を表示
                        st.warning("⚠️ 手書き機能がエラーを起こしたため、通常表示に切り替えます。")
                        st.image(img, width=display_width)
                else:
                    st.image(img, width=display_width)

    # --- 右: 操作と表 ---
    with col2:
        st.subheader("📊 抽出結果")
        
        if st.button("抽出を開始する", type="primary", use_container_width=True):
            st.info(f"処理ページ数: {len(input_contents)}枚")
            
            try:
                model = genai.GenerativeModel(selected_model_name)
                processed_inputs = []
                for img in input_contents:
                    if st.session_state['rotation'] != 0:
                        img = img.rotate(st.session_state['rotation'], expand=True)
                    processed_inputs.append(img)
                
                prompt = """
                請求書画像を解析し、JSON形式で出力してください。Markdownは不要。
                
                1. **items**: 明細リスト (日付, 燃料名, 使用量(L), 請求額(円))
                   - **抽出対象**: ガソリン, 軽油, 灯油, 重油などCO2排出燃料のみ。
                   - **除外**: 電気, ガス, 水道, オイル交換, タイヤ, 工賃, 部品, 洗車。
                   - 合計行は除外。
                2. **tax_type**: "税込" または "税抜"
                """
                
                with st.spinner("解析中..."):
                    # 暴走防止のため max_output_tokens を設定
                    res = model.generate_content(
                        [prompt] + processed_inputs,
                        generation_config=genai.types.GenerationConfig(max_output_tokens=4000)
                    )
                    full_data = extract_json(res.text)
                    
                    if full_data:
                        raw_items = full_data.get("items", [])
                        cleaned_items = clean_and_normalize_data(raw_items)
                        df = pd.DataFrame(cleaned_items)
                    else:
                        df = pd.DataFrame() # 空でも作成

                    # --- 重要: KeyError防止のための列保証 ---
                    required_columns = ["日付", "燃料名", "使用量", "請求額"]
                    if df.empty:
                        df = pd.DataFrame(columns=required_columns)
                    else:
                        # 足りない列があれば 0 や空文字で埋める
                        for col in required_columns:
                            if col not in df.columns:
                                df[col] = 0 if col in ["使用量", "請求額"] else ""
                        # 余計な列は捨てる
                        df = df[required_columns]

                    df.reset_index(drop=True, inplace=True)
                    st.session_state['df'] = df
                    st.session_state['tax_type'] = full_data.get("tax_type", "不明") if full_data else "不明"
                    st.toast("完了", icon="✅")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

        # --- 表の表示処理 ---
        if 'df' in st.session_state:
            df = st.session_state['df']
            
            # DataFrameが壊れていないか最終チェック
            required_cols = ["使用量", "請求額", "燃料名", "日付"]
            is_valid_df = not df.empty and all(c in df.columns for c in required_cols)

            if is_valid_df:
                # 安全に型変換
                df["使用量"] = pd.to_numeric(df["使用量"], errors='coerce').fillna(0)
                df["請求額"] = pd.to_numeric(df["請求額"], errors='coerce').fillna(0)
                df["日付"] = df["日付"].astype(str).replace("nan", "")
                df["燃料名"] = df["燃料名"].astype(str).replace("nan", "")

                st.markdown(f"**💰 消費税:** `{st.session_state.get('tax_type', '不明')}`")
                
                # 1. 集計表
                st.markdown("##### 📈 集計テーブル")
                summary_df = df.groupby("燃料名")[["使用量", "請求額"]].sum().reset_index()
                total_usage = summary_df["使用量"].sum()
                total_cost = summary_df["請求額"].sum()
                total_row = pd.DataFrame({
                    "燃料名": ["🔴 総合計"], 
                    "使用量": [total_usage], 
                    "請求額": [total_cost]
                })
                display_summary = pd.concat([summary_df, total_row], ignore_index=True)
                
                st.dataframe(
                    display_summary, 
                    use_container_width=True, 
                    hide_index=True,
                    column_config={
                        "請求額": st.column_config.NumberColumn(format="¥%d"),
                        "使用量": st.column_config.NumberColumn(format="%.2f L"),
                    }
                )
                
                st.markdown("---")
                st.markdown("##### 📝 詳細データ")

                # 2. フィルタ
                unique_fuels = df["燃料名"].unique().tolist()
                selected_fuels = st.multiselect("🔍 燃料名でフィルタ", unique_fuels, default=unique_fuels)
                
                view_df = df if not selected_fuels else df[df["燃料名"].isin(selected_fuels)]

                # 3. エディタ
                edited_df = st.data_editor(
                    view_df,
                    num_rows="dynamic", 
                    use_container_width=True,
                    hide_index=True,
                    key="editor_safe_v1", 
                    column_config={
                        "日付": st.column_config.TextColumn("日付"),
                        "燃料名": st.column_config.TextColumn("燃料名"),
                        "請求額": st.column_config.NumberColumn("請求額(円)", format="¥%d"),
                        "使用量": st.column_config.NumberColumn("使用量(L)", format="%.2f L"),
                    }
                )
                
                # 4. 同期
                if not edited_df.equals(view_df):
                    new_main_df = st.session_state['df'].copy()
                    
                    # 削除
                    deleted_indices = set(view_df.index) - set(edited_df.index)
                    if deleted_indices:
                        new_main_df = new_main_df.drop(list(deleted_indices))
                    
                    # 更新
                    common_indices = list(set(edited_df.index) & set(new_main_df.index))
                    if common_indices:
                        new_main_df.update(edited_df.loc[common_indices])
                    
                    # 追加
                    new_rows = edited_df[~edited_df.index.isin(view_df.index)]
                    if not new_rows.empty:
                        new_main_df = pd.concat([new_main_df, new_rows], ignore_index=True)
                    
                    st.session_state['df'] = new_main_df.reset_index(drop=True)
                    st.rerun()

                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("CSVダウンロード", csv, "fuel_data.csv", "text/csv", use_container_width=True)
