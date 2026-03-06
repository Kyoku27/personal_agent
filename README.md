# 个人智能体 (Personal Agent)

EC販売の各種データを自動取得・集計し、Lark（飞书）の多維表格にリアルタイムで書き込む個人用自動化エージェント。

---

## 環境セットアップ

Anaconda 環境 `personal_agent` で開発・実行。

```bash
conda activate personal_agent
cd c:\Projects\personal_agent
pip install -r requirements.txt
```

> Cursor 内蔵ターミナルで `conda` が認識されない場合は「Anaconda Prompt」を使うか、VS Code/Cursor のインタープリター設定で `personal_agent` 環境を選択してください。

---

## 機能一覧

### 1. 楽天 → Lark 多维表格 自動同期 ⭐ New

楽天 RMS 受注 API から SKU（商品管理番号）単位で日別売上を取得し、Lark の横型ピボットテーブルへ自動書き込みする。

**設定（`.env` ファイル）：**

```env
# Feishu / Lark
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_BITABLE_APP_TOKEN=xxxxxxxxxxxxxxxx  # BaseのURL内から取得
FEISHU_BITABLE_TABLE_ID=xxxxxxxxxxxxxxxx  # BaseのURL内から取得

# 楽天 RMS
RAKUTEN_API_KEY=xxxxxxxxxxxxxxxx  # serviceSecret:licenseKey
RAKUTEN_SHOP_ID=your_shop_id
RAKUTEN_BASE_URL=https://api.rms.rakuten.co.jp
```

**実行方法：**

```bash
# 昨日分を同期（デフォルト）
python run_rakuten_sync.py

# 特定日付を指定して同期
python run_rakuten_sync.py --date 2026-02-22

# Lark テーブルの列名を確認（デバッグ用）
python run_rakuten_sync.py --inspect
```

**動作フロー：**
1. 楽天 `searchOrder` API → 指定日の受注番号一覧を取得（全ページ対応）
2. 楽天 `getOrder` API → SKU（`manageNumber`）・数量・金額を抽出
3. Lark 多维表格：`商品名` 列でSKUを検索 → 行があれば `{day}日` 列を更新、なければ新規行作成

---

### 2. Amazon 排名 → Lark 电子表格 同步

从飞书电子表格 A 列读取 ASIN，抓取 Amazon 日本站排名，写回当日列和 F 列（小类目）。

**設定（`.env` ファイル）：**

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_SHEET_TOKEN=xxxxxxxxxxxxxxxx   # 电子表格 URL 中 /sheets/ 后面的 token
FEISHU_SHEET_NAME=3月                  # Sheet 名称，默认当前月份
```

**実行方法：**

```bash
# 默认 Sheet（FEISHU_SHEET_NAME 或当前月份）
python run_amazon_rank.py

# 指定 Sheet 名称
python run_amazon_rank.py --sheet 3月
```

---

### 3. ページ分析

Web ページの SEO タイトル・meta 描述・H1・Open Graph などを取得。

```bash
python run_page_analysis.py https://www.example.com
```

---

## プロジェクト構成

```
personal_agent/
├── .env.example              # 環境変数テンプレート（機密情報は .env に記述）
├── .gitignore                # .env を Git 管理対象外に指定
├── config.yaml               # Lark Bitable フィールドマッピング設定
├── requirements.txt          # Python 依存ライブラリ
├── run_rakuten_sync.py       # 楽天→Lark 同期スクリプト
├── run_amazon_rank.py        # Amazon 排名→Lark Sheet 同期
├── run_page_analysis.py      # ページ分析スクリプト
└── src/
    ├── core/
    │   └── config_manager.py         # 環境変数・YAML 設定ローダー
    └── features/
        ├── ecommerce/
        │   └── rakuten/
        │       ├── api_client.py     # 楽天 RMS API クライアント
        │       └── data_analyzer.py  # 受注データ集計
        └── feishu/
            ├── bot_client.py         # Lark 認証（tenant_access_token）
            ├── sheet_manager.py      # Lark 多维表格 CRUD
            └── amazon_rank_sync.py   # Amazon 排名→Lark 电子表格
```

---

## 注意事項

- **`.env` ファイルは絶対に Git にコミットしないこと**（`.gitignore` で除外済み）
- Lark アプリに `base:record:create` / `base:record:retrieve` / `base:record:update` の権限が必要
- 楽天 RMS API は `serviceSecret:licenseKey` を Base64 エンコードした ESA 認証を使用
