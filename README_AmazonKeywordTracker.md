# Amazon 关键词追踪 (Keyword Tracker)

这个模块用于自动抓取指定 ASIN 和关键词在 Amazon 日本站（amazon.co.jp）上的排名情况，包括**自然位 (Organic)** 和 **广告位 (Ad)**，并将结果追加写入到指定的飞书多维表格（Lark Sheet）中。

## ⚙️ 准备工作：环境变量配置

请在项目根目录的 `.env` 文件中配置以下变量：

```ini
# 飞书应用凭证
LARK_HOST=https://open.larksuite.com
FEISHU_APP_ID=cli_axxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx

# 关键词追踪表格专属 Token 与 Sheet 名称配置
FEISHU_KEYWORD_SHEET_TOKEN=KbyvsiPLyhZufEtakX4jPypRpoh  # 你的追踪表格的 Token
FEISHU_KEYWORD_SHEET_NAME=KW追踪                 # 存放数据结果的 Sheet 名称，默认 KW追踪
```

## 📊 飞书表格 (Lark Sheet) 结构要求

你的飞书表格必须包含以下两个 Sheet：

### 1. `Master` (人工维护的关键词库)
该 Sheet 用于读取你需要追踪的 ASIN 和关键词。
**必须在第一行配置正确的英文表头（不区分大小写，列顺序可变）**：

| brand | asin | product | keyword |
| :--- | :--- | :--- | :--- |
| MOFT | B0891YT5WB | モフト スマホスタンド | moft |
|      |          |               | moft x |
| MOFT | B0CHW9BMGQ | Snap-On       | magsafe moft スタンド |

* **💡 小技巧**：`asin`, `brand`, `product` 列可以留空。如果为空，程序会自动继承（Carry-forward）上一行的内容。这非常适合为一个 ASIN 批量配置多个 keywords。

### 2. `KW追踪` (程序自动追加结果的表格)
该 Sheet 用于保存代码抓取的结果。（名字需要和 `FEISHU_KEYWORD_SHEET_NAME` 保持一致）
**表头如下：**

| date | brand | asin | product | keyword | rank_type | rank |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2026-03-13 | MOFT | B0891YT5WB | モフト... | moft | organic | 58 |
| 2026-03-13 | MOFT | B0891YT5WB | モフト... | moft | ad | 999 |
| 2026-03-13 | MOFT | B0891YT5WB | モフト... | moft x | organic | 25 |

* **`rank_type` 释义**：
  * `organic`: 自然搜索排名（排除了广告位计算）
  * `ad`: 广告位排名（仅仅在广告区域内的顺位）
* **`rank` = 999**: 表示在设定的最大翻页范围内（如前3页），没有找到该 ASIN 的对应词排名。

---

## 🚀 运行命令

首先，请确保进入了正确的环境并到了代码目录下：
```powershell
conda activate personal_agent
cd C:\Projects\agent
```

### 测试运行 (Dry-run)🚨 推荐的首次调试方式
只抓取 Amazon 数据并打印到终端窗口，**不会写入修改你的飞书表格**。用来检查爬虫是否正常或者是否被 Amazon 屏蔽。
```powershell
python run_amazon_keyword.py --dry-run
```

### 正式运行 (并写入飞书)
执行抓取，并将所有包含自然排名和广告排名的数据**追加**到 `KW追踪` Sheet 的末尾。
```powershell
python run_amazon_keyword.py
```

### 自定义写入的 Sheet 名
如果本次运行你想写入到一个全新的 Sheet（例如叫 `测试结果`），可以直接在命令行覆盖：
```powershell
python run_amazon_keyword.py --sheet 测试结果
```

---

## 🛑 常见报错与排查

1. **`Forbidden (code: 91403)`**
   * **原因**：你的飞书应用 `FEISHU_APP_ID` 没有写入这个表格的权限。
   * **解决**：打开飞书表格点击右上方「共享」，添加你的机器人应用为「编辑者」，如果还不行，请去 Lark 开发者后台确认该应用是否开启了 `sheets:spreadsheet` 的 Write 权限并发布了最新版本。

2. **`Amazon returned 503 (Anti-scraping)`**
   * **原因**：爬取过快，被 Amazon 判定为机器人导致拦截。
   * **解决**：稍后再试，或者如果频繁出现需要考虑部署代理 IP（Proxy）或增大抓取的 `time.sleep` 间隔。

3. **`Sheet 'Master' not found in Lark`**
   * **原因**：表格中没有名字叫做 `Master` 的工作表。
   * **解决**：仔细检查表格的标签页，注意是否有大写/空格。

5. **无数据抓取 / 关键词全被跳过**
   * **原因**：检查你的 `Master` 表格是否真的写入了数据，程序识别到空关键词会跳过；有时候飞书返回的是空值。我们已经用 `Carry-forward` 逻辑解决了留空 ASIN 的问题。

---

## 🧠 原理说明：如何区分自然位和广告位？

Amazon 的搜索结果页面（HTML 结构）中，自然搜索结果和广告（Sponsored）结果的标签属性是不一样的。我们的代码利用 `BeautifulSoup` 对搜索页面的 DOM 进行解析来区分这两种排名。

### 1. 自然位 (Organic Rank) 检测
程序通过 `extract_organic_rank` 函数来计算自然排名：
- 抓取页面上所有带有 `data-asin` 属性的商品卡片 `<div data-asin="...">`。
- **排除广告特征**：如果在该卡片的 `class` 中检测到 `"AdHolder"`（旧版广告标识）或者 `"sp-sponsored-result"`（新版广告标识），则直接跳过，**不计入排名序号**。
- 按顺序匹配目标 `ASIN`，找到匹配项时的序号即为自然位。

### 2. 广告位 (Ad Rank) 检测
程序通过 `extract_ad_rank` 函数来计算广告排名：
- 同样获取所有商品卡片。
- **匹配广告特征**：通过以下条件判定一个卡片是否为广告：
  1. 卡片的 `class` 包含 `"AdHolder"` 或 `"sp-sponsored-result"`。
  2. 卡片内部包含特定的广告标签组件（如 `.puis-label-popover-default` 或 `.s-label-popover-default`），且其文本包含日语 **"スポンサー"** (Sponsored)。
  3. 卡片前200个字符的内容中直接包含 **"スポンサー"** 字样。
- 如果确认是广告卡片，才进行序号计数（跳过所有普通的自然搜索结果）。当在此类卡片中匹配到目标 `ASIN` 时，返回的即为广告位顺位。

这种机制确保了对于同一个词搜索出来的同一款商品：
- 分配的 `organic` 名次代表它在纯自然搜索流中的绝对名次。
- 分配的 `ad` 名次代表它在所有竞价广告位中排在第几个。
