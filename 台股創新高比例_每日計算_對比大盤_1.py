"""
台股創新高價股數量比 — 每日計算腳本（含大盤指數比對版）
=====================================================
功能：
1. 從證交所/櫃買中心取得所有上市櫃股票代碼
2. 透過 yfinance 下載過去一年的歷史資料
3. 計算「今日最高價 = 過去一年最高價」的股票佔比
4. 同時取得台灣加權指數（大盤）每日收盤價
5. 把每天的計算結果（含大盤指數）追加存入 CSV 檔案
6. 用最新的 CSV 資料產生「雙 Y 軸」互動式 HTML 圖表
   - 左 Y 軸：創新高比例 (%)
   - 右 Y 軸：大盤加權指數收盤點數
"""

import urllib3
import yfinance as yf
import pandas as pd
import requests
import warnings
import json
import io
import os
from datetime import datetime

# ===== 關閉不必要的警告訊息 =====
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

# ===== 設定檔案路徑（跟這支腳本放在同一個資料夾） =====
# os.path.dirname(__file__) 會取得這支 .py 檔所在的資料夾路徑
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(SCRIPT_DIR, "台股創新高比例_歷史資料.csv")
HTML_PATH = os.path.join(SCRIPT_DIR, "台股創新高比例_圖表.html")


def get_tw_tickers():
    """
    從證交所與櫃買中心的公開網頁，取得所有上市（.TW）與上櫃（.TWO）的股票代碼。
    回傳格式範例：['2330.TW', '2317.TW', '6510.TWO', ...]
    """
    tickers = []

    # 上市股票網址（strMode=2）與上櫃股票網址（strMode=4）
    url_twse = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    url_tpex = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"

    for url, suffix in [(url_twse, ".TW"), (url_tpex, ".TWO")]:
        try:
            # verify=False 是因為該網站的 SSL 憑證有時會出問題
            res = requests.get(url, verify=False)

            # 用 pandas 解析 HTML 表格
            dfs = pd.read_html(io.StringIO(res.text))
            df = dfs[0]

            # 手動指定欄位名稱，避免網頁原始碼夾帶看不見的空白字元
            df.columns = ['代號及名稱', 'ISIN', '上市日', '市場別', '產業別', 'CFICode', '備註']

            # 第一列是原始表頭，跳過
            df = df.iloc[1:]

            # 移除 CFICode 為空的列（這些是分類標題，不是股票）
            df = df.dropna(subset=['CFICode'])

            # CFICode 以 'ES' 開頭的才是真正的股票（排除 ETF、權證等）
            df = df[df['CFICode'].str.startswith('ES', na=False)]

            # 代號和名稱之間是全形空白 '　'，只取代號部分
            codes = df['代號及名稱'].str.split('　').str[0].tolist()

            # 組合成 yfinance 需要的格式，例如 '2330.TW'
            tickers.extend([f"{code}{suffix}" for code in codes])

        except Exception as e:
            print(f"⚠ 獲取 {suffix} 代碼時發生錯誤: {e}")

    return tickers


def get_taiex_close():
    """
    從 Yahoo Finance 下載台灣加權指數（代碼 ^TWII）的最新收盤價。

    什麼是 ^TWII？
    - 就是「台灣加權股價指數」在 Yahoo Finance 上的代碼
    - 也就是大家常說的「大盤指數」、「加權指數」
    - 例如新聞說「今天大盤收在 22000 點」，指的就是這個

    回傳值：最新一個交易日的收盤點數（float），若下載失敗則回傳 None。
    """
    try:
        print("📡 正在下載台灣加權指數（^TWII）資料...")

        # period="5d" 只抓最近 5 天，確保至少能拿到最新一個交易日的資料
        # （假日不開盤，所以抓 5 天比較保險）
        index_data = yf.download("^TWII", period="5d", interval="1d", auto_adjust=True)

        # 如果完全沒有資料（例如網路問題），回傳 None
        if index_data.empty:
            print("⚠ 無法取得大盤指數資料")
            return None

        # 取得最新一天的收盤價（Close 欄位）
        # .iloc[-1] 代表最後一列（也就是最新的交易日）
        # 取得最新一天的收盤價（Close 欄位）
        latest_close_data = index_data['Close'].iloc[-1]
        
        # 判斷如果是 Series (新版 yfinance)，就取出該列裡面的第一個數值；否則直接轉 float (舊版)
        if isinstance(latest_close_data, pd.Series):
            latest_close = float(latest_close_data.iloc[0])
        else:
            latest_close = float(latest_close_data)

        # 四捨五入到小數點後兩位
        latest_close = round(latest_close, 2)
        print(f"✅ 大盤指數收盤價: {latest_close}")
        return latest_close

    except Exception as e:
        print(f"⚠ 下載大盤指數時發生錯誤: {e}")
        return None


def calculate_new_high_ratio():
    """
    核心計算函式：
    1. 取得所有台股代碼
    2. 下載過去一年的歷史最高價資料
    3. 計算「最新一天的最高價 = 過去一年最高價」的股票數量佔比
    4. 同時取得大盤加權指數收盤價
    5. 回傳 (日期, 有效股票數, 創新高數, 百分比, 大盤指數) 的 tuple
    """
    print("📡 正在獲取台股上市櫃股票清單...")
    tickers = get_tw_tickers()

    if not tickers:
        print("❌ 沒有獲取到股票代碼，程式結束。")
        return None

    print(f"✅ 成功獲取 {len(tickers)} 檔股票。正在下載過去一年的歷史資料（這可能需要幾分鐘）...")

    # yf.download 可以一次批次下載所有股票，threads=True 啟用多線程加速
    # period="1y" 代表過去一年（大約 252 個交易日）
    data = yf.download(tickers, period="1y", interval="1d", auto_adjust=True, threads=True)

    # ===== 同步取得大盤加權指數收盤價 =====
    taiex_close = get_taiex_close()

    new_high_count = 0      # 創新高的股票數量
    valid_stock_count = 0   # 有足夠歷史資料的股票數量

    print("🔍 正在計算創新高比例...")

    for ticker in tickers:
        try:
            # 取出這檔股票每天的「最高價」，移除缺失值（沒有交易的日子）
            high_prices = data['High'][ticker].dropna()

            # 若交易天數不到 200 天，代表上市不滿一年，不納入計算
            if len(high_prices) < 200:
                continue

            valid_stock_count += 1

            # 最新一天的最高價
            latest_high = high_prices.iloc[-1]
            # 過去一年的最高價
            year_high = high_prices.max()

            # 如果最新最高價 >= 年度最高價，代表今天創了新高
            if latest_high >= year_high:
                new_high_count += 1

        except KeyError:
            # 有些股票在 Yahoo Finance 上沒有資料，跳過
            continue

    if valid_stock_count == 0:
        print("❌ 沒有足夠的有效股票資料可供計算。")
        return None

    # 計算百分比
    ratio = round((new_high_count / valid_stock_count) * 100, 2)
    today = datetime.now().strftime("%Y-%m-%d")

    print("-" * 40)
    print(f"📅 日期: {today}")
    print(f"📊 有效樣本數: {valid_stock_count} 檔")
    print(f"🔺 創新高股票數: {new_high_count} 檔")
    print(f"📈 台股創新高價股數量比: {ratio}%")
    # 顯示大盤指數（如果有成功取得的話）
    if taiex_close is not None:
        print(f"📉 大盤加權指數收盤: {taiex_close}")

    # 回傳值比原本多了一個 taiex_close（大盤指數收盤價）
    return today, valid_stock_count, new_high_count, ratio, taiex_close


def save_to_csv(date, valid_count, new_high_count, ratio, taiex_close):
    """
    把今天的計算結果追加到 CSV 檔案。
    如果 CSV 檔不存在，就自動建立並寫入表頭。
    如果今天已經有紀錄，就覆蓋（避免重複執行產生重複資料）。

    參數說明：
    - date: 日期字串，例如 '2026-04-02'
    - valid_count: 有效股票數（int）
    - new_high_count: 創新高股票數（int）
    - ratio: 創新高比例百分比（float）
    - taiex_close: 大盤加權指數收盤價（float 或 None）
    """
    # 準備這次要寫入的資料（一列 DataFrame）
    # 新增了「大盤指數」欄位
    new_row = pd.DataFrame([{
        "日期": date,
        "有效股票數": valid_count,
        "創新高股票數": new_high_count,
        "創新高比例(%)": ratio,
        "大盤指數": taiex_close  # 新增：大盤加權指數收盤價
    }])

    if os.path.exists(CSV_PATH):
        # 讀取現有的 CSV 檔案
        df = pd.read_csv(CSV_PATH)

        # 如果舊 CSV 沒有「大盤指數」欄位（相容舊版資料），自動補上空值
        if "大盤指數" not in df.columns:
            df["大盤指數"] = None

        # 如果今天已經有紀錄，先移除舊的（避免重複）
        df = df[df["日期"] != date]

        # 把新資料加到最後面
        df = pd.concat([df, new_row], ignore_index=True)
    else:
        # CSV 檔不存在，直接用新資料建立
        df = new_row

    # 按日期排序後存檔
    df = df.sort_values("日期").reset_index(drop=True)
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"💾 資料已儲存至: {CSV_PATH}")

    return df


def generate_html_chart(df):
    """
    根據 CSV 中的歷史資料，產生一個「雙 Y 軸」互動式 HTML 折線圖。
    - 左 Y 軸（金色線）：創新高比例 (%)
    - 右 Y 軸（青色線）：大盤加權指數收盤點數

    使用 Chart.js 函式庫，可以在瀏覽器中檢視、縮放、懸停查看數值。
    """
    # 準備圖表需要的資料
    dates = df["日期"].tolist()
    ratios = df["創新高比例(%)"].tolist()
    new_highs = df["創新高股票數"].tolist()
    valid_counts = df["有效股票數"].tolist()

    if "大盤指數" in df.columns:
        # 將 NaN 替換為 None (轉成 JSON 時才會正確變成 null)
        taiex_values = df["大盤指數"].astype(object).where(pd.notnull(df["大盤指數"]), None).tolist()
    else:
        taiex_values = [None] * len(dates)

    # 使用 json.dumps() 將 Python 列表轉換為合法的 JavaScript 陣列字串

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>台股創新高價股數量比 vs 大盤指數</title>
    <!-- 引入 Chart.js 函式庫（用來畫圖的工具） -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
    <style>
        /* ===== 頁面基本樣式 ===== */
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Microsoft JhengHei', 'PingFang TC', sans-serif;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            min-height: 100vh;
            padding: 20px;
            color: #e0e0e0;
        }}
        .container {{
            max-width: 1100px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            font-size: 28px;
            margin-bottom: 8px;
            background: linear-gradient(90deg, #f7971e, #ffd200);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            text-align: center;
            color: #888;
            font-size: 14px;
            margin-bottom: 24px;
        }}

        /* ===== 統計卡片區 ===== */
        .stats-row {{
            display: flex;
            gap: 16px;
            margin-bottom: 24px;
            flex-wrap: wrap;
            justify-content: center;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px;
            padding: 16px 24px;
            text-align: center;
            min-width: 160px;
            flex: 1;
            max-width: 220px;
        }}
        .stat-card .label {{
            font-size: 13px;
            color: #999;
            margin-bottom: 6px;
        }}
        .stat-card .value {{
            font-size: 26px;
            font-weight: bold;
        }}
        .stat-card .value.highlight {{
            color: #ffd200;
        }}
        .stat-card .value.green {{
            color: #4caf50;
        }}
        .stat-card .value.blue {{
            color: #42a5f5;
        }}
        .stat-card .value.cyan {{
            color: #26c6da;
        }}

        /* ===== 圖表容器 ===== */
        .chart-wrapper {{
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
        }}
        canvas {{
            width: 100% !important;
        }}

        /* ===== 表格區 ===== */
        .table-wrapper {{
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 20px;
            overflow-x: auto;
        }}
        .table-wrapper h2 {{
            font-size: 18px;
            margin-bottom: 12px;
            color: #ccc;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th {{
            background: rgba(255,255,255,0.08);
            padding: 10px 12px;
            text-align: center;
            color: #aaa;
            font-weight: 600;
        }}
        td {{
            padding: 10px 12px;
            text-align: center;
            border-bottom: 1px solid rgba(255,255,255,0.06);
        }}
        tr:hover td {{
            background: rgba(255,255,255,0.04);
        }}
        .footer {{
            text-align: center;
            color: #555;
            font-size: 12px;
            margin-top: 16px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>台股創新高價股數量比 vs 大盤指數</h1>
        <p class="subtitle">每日追蹤：創 52 週新高股票佔比（金色）與加權指數走勢（青色）</p>

        <!-- 統計卡片：新增了「大盤指數」卡片 -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="label">大盤指數</div>
                <div class="value cyan" id="latest-taiex">—</div>
            </div>
            <div class="stat-card">
                <div class="label">創新高比例</div>
                <div class="value highlight" id="latest-ratio">—</div>
            </div>
            <div class="stat-card">
                <div class="label">創新高股票數</div>
                <div class="value green" id="latest-count">—</div>
            </div>
            <div class="stat-card">
                <div class="label">有效樣本數</div>
                <div class="value blue" id="latest-valid">—</div>
            </div>
            <div class="stat-card">
                <div class="label">資料天數</div>
                <div class="value" id="total-days">—</div>
            </div>
        </div>

        <!-- 雙 Y 軸折線圖 -->
        <div class="chart-wrapper">
            <canvas id="mainChart" height="420"></canvas>
        </div>

        <!-- 歷史資料表格：新增「大盤指數」欄位 -->
        <div class="table-wrapper">
            <h2>歷史資料</h2>
            <table id="dataTable">
                <thead>
                    <tr>
                        <th>日期</th>
                        <th>大盤指數</th>
                        <th>有效股票數</th>
                        <th>創新高股票數</th>
                        <th>創新高比例 (%)</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>

        <p class="footer">資料來源：Yahoo Finance ｜ 最後更新：<span id="last-update"></span></p>
    </div>

    <script>
        // ===== 嵌入從 CSV 讀取的資料 =====
        const dates = {json.dumps(dates)};
        const ratios = {json.dumps(ratios)};
        const newHighs = {json.dumps(new_highs)};
        const validCounts = {json.dumps(valid_counts)};
        const taiexValues = {json.dumps(taiex_values)};

        // ===== 更新統計卡片上的數字 =====
        const lastIdx = dates.length - 1;
        document.getElementById('latest-ratio').textContent = ratios[lastIdx] + '%';
        document.getElementById('latest-count').textContent = newHighs[lastIdx] + ' 檔';
        document.getElementById('latest-valid').textContent = validCounts[lastIdx] + ' 檔';
        document.getElementById('total-days').textContent = dates.length + ' 天';
        document.getElementById('last-update').textContent = dates[lastIdx];

        // 大盤指數卡片：如果有資料就顯示數字，沒有就顯示「—」
        const latestTaiex = taiexValues[lastIdx];
        document.getElementById('latest-taiex').textContent =
            latestTaiex !== null ? latestTaiex.toLocaleString() : '—';

        // ===== 用 Chart.js 繪製「雙 Y 軸」折線圖 =====
        const ctx = document.getElementById('mainChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',  // 折線圖
            data: {{
                labels: dates,  // X 軸：日期
                datasets: [
                    {{
                        // 第一條線：創新高比例（綁定到左 Y 軸 'y'）
                        label: '創新高比例 (%)',
                        data: ratios,
                        borderColor: '#ffd200',                    // 金色線條
                        backgroundColor: 'rgba(255,210,0,0.08)',   // 半透明金色填充
                        borderWidth: 2,
                        pointRadius: 3,
                        pointHoverRadius: 6,
                        pointBackgroundColor: '#ffd200',
                        fill: true,
                        tension: 0.3,
                        yAxisID: 'y'   // 綁定到左 Y 軸
                    }},
                    {{
                        // 第二條線：大盤指數（綁定到右 Y 軸 'y1'）
                        label: '大盤加權指數',
                        data: taiexValues,
                        borderColor: '#26c6da',                    // 青色線條
                        backgroundColor: 'rgba(38,198,218,0.08)',  // 半透明青色填充
                        borderWidth: 2,
                        pointRadius: 3,
                        pointHoverRadius: 6,
                        pointBackgroundColor: '#26c6da',
                        fill: true,
                        tension: 0.3,
                        yAxisID: 'y1',  // 綁定到右 Y 軸
                        // spanGaps: true 代表如果某天沒有資料（null），
                        // Chart.js 會自動跳過並把前後兩點連起來
                        spanGaps: true
                    }}
                ]
            }},
            options: {{
                responsive: true,          // 自適應視窗大小
                maintainAspectRatio: false,
                interaction: {{
                    // 讓滑鼠懸停時，兩條線的提示框同時顯示
                    mode: 'index',
                    intersect: false
                }},
                plugins: {{
                    legend: {{
                        labels: {{ color: '#ccc', font: {{ size: 14 }} }}
                    }},
                    tooltip: {{
                        // 滑鼠懸停時顯示的提示框
                        callbacks: {{
                            afterLabel: function(context) {{
                                const i = context.dataIndex;
                                // 只在「創新高比例」那條線上顯示額外資訊
                                if (context.datasetIndex === 0) {{
                                    return '創新高: ' + newHighs[i] + ' / ' + validCounts[i] + ' 檔';
                                }}
                                return '';
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        ticks: {{ color: '#888', maxRotation: 45 }},
                        grid: {{ color: 'rgba(255,255,255,0.05)' }}
                    }},
                    // ===== 左 Y 軸：創新高比例 (%) =====
                    y: {{
                        type: 'linear',
                        position: 'left',     // 放在左邊
                        ticks: {{
                            color: '#ffd200',  // 金色刻度（跟線條同色，方便辨認）
                            callback: function(v) {{ return v + '%'; }}
                        }},
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: '創新高比例 (%)',
                            color: '#ffd200',
                            font: {{ size: 13 }}
                        }}
                    }},
                    // ===== 右 Y 軸：大盤加權指數 =====
                    y1: {{
                        type: 'linear',
                        position: 'right',    // 放在右邊
                        ticks: {{
                            color: '#26c6da',  // 青色刻度（跟線條同色）
                            callback: function(v) {{ return v.toLocaleString(); }}
                        }},
                        grid: {{
                            // 右 Y 軸的格線設為不顯示，避免跟左 Y 軸的格線重疊
                            drawOnChartArea: false
                        }},
                        title: {{
                            display: true,
                            text: '大盤加權指數',
                            color: '#26c6da',
                            font: {{ size: 13 }}
                        }}
                    }}
                }}
            }}
        }});

        // ===== 填入歷史資料表格（從最新到最舊排列） =====
        const tbody = document.querySelector('#dataTable tbody');
        for (let i = dates.length - 1; i >= 0; i--) {{
            const tr = document.createElement('tr');
            // 大盤指數如果是 null 就顯示「—」，否則顯示千分位格式的數字
            const taiexDisplay = taiexValues[i] !== null
                ? taiexValues[i].toLocaleString()
                : '—';
            tr.innerHTML = `<td>${{dates[i]}}</td><td>${{taiexDisplay}}</td><td>${{validCounts[i]}}</td><td>${{newHighs[i]}}</td><td>${{ratios[i]}}%</td>`;
            tbody.appendChild(tr);
        }}
    </script>
</body>
</html>"""

    # 將 HTML 內容寫入檔案
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"📊 互動式圖表已產生: {HTML_PATH}")


# ===== 主程式 =====
if __name__ == "__main__":
    print("=" * 50)
    print("  台股創新高價股數量比 — 每日計算（含大盤指數）")
    print("=" * 50)

    # 第一步：計算今天的創新高比例（同時取得大盤指數）
    result = calculate_new_high_ratio()

    if result:
        # 解包回傳值：比原本多了一個 taiex_close（大盤指數收盤價）
        date, valid_count, new_high_count, ratio, taiex_close = result

        # 第二步：把結果存入 CSV（含大盤指數）
        df = save_to_csv(date, valid_count, new_high_count, ratio, taiex_close)

        # 第三步：用最新的完整資料產生「雙 Y 軸」HTML 圖表
        generate_html_chart(df)

        print("\n✅ 全部完成！")
    else:
        print("\n❌ 計算失敗，請檢查網路連線或稍後再試。")
