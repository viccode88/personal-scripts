## 電子書轉換與翻譯工具（trans）

提供三個小工具：

- `book_finder.py`：以書名關鍵字從公開資料源查找最相符的書籍資訊（僅檢索，不下載）。
- `convert_to_epub.py`：批量將 `.mobi`/`.azw3` 轉為 `.epub`（需要 Calibre 的 `ebook-convert`）。
- `mobi_epub_preserve_format_translate.py`：將 `.mobi` 或 `.epub` 的可見文字翻譯為指定語言，盡量保持原始排版/圖片/CSS 不變（需要 OpenAI API）。

### 安裝需求
- Python 3.9+
- 建議安裝套件：`requests`, `beautifulsoup4`, `lxml`, `openai`
- 轉檔：需安裝 Calibre 並提供 `ebook-convert` 指令於 PATH（或使用預設安裝路徑）
- 翻譯：需設定環境變數 `OPENAI_API_KEY`

---

### 1) 書籍查找 `book_finder.py`

依序查詢 Open Library、Project Gutenberg（gutendex）、Internet Archive，整合候選並以 token overlap 分數挑出最相符結果，輸出 `results.csv` 與 `results.json`。

#### 基本用法
```bash
python book_finder.py "書名1, 書名2, 書名3"
```

#### 行為說明
- 顯示每個查詢的最佳匹配（來源、標題、作者、年份、連結）。
- 所有候選會寫入 `results.json`；摘要寫入 `results.csv`。
- 僅檢索公開來源，不進行下載。

#### 範例
```bash
python book_finder.py "Pride and Prejudice, The Picture of Dorian Gray"
```

---

### 2) 格式轉換 `convert_to_epub.py`

從指定根目錄遞迴搜尋 `.mobi`/`.azw3`，以 Calibre 的 `ebook-convert` 轉為 `.epub`，具備「已存在且較新則跳過」與多線程併發。

#### 基本用法
（此腳本以程式內的 `BASE_DIR` 做為掃描根目錄，請視需求修改程式內常數）

```bash
python convert_to_epub.py
```

#### 參數/設定
- 程式內常數：
  - `BASE_DIR`：掃描根目錄（預設為作者本機路徑，請修改）
  - `EXTS`：來源副檔名集合（預設 `{.mobi, .azw3}`）
  - `MAX_WORKERS`：併發度（預設 8）

#### 行為說明與訊息
- 若目的 `.epub` 已存在且較新，會顯示 `skip (already up-to-date)`。
- 轉檔成功顯示 `ok`，失敗會列於結算的「失敗清單」。

---

### 3) 保留排版的翻譯 `mobi_epub_preserve_format_translate.py`

將 `.mobi` 先轉為 `.epub`（或直接用 `.epub`），抽取 XHTML/XML 可見文字與 `<img alt>`，呼叫 OpenAI 進行翻譯，最後回填並重新封裝 EPUB。

#### 基本用法
```bash
python mobi_epub_preserve_format_translate.py INPUT(.mobi|.epub) \
  --target zh-TW --model gpt-5 [--skip-convert] \
  [--max-chars-per-call 3500] [--preview-limit 0] [--max-workers 10]
```

#### 參數說明
- `input`：輸入檔，支援 `.mobi` 或 `.epub`
- `--target`：目標語言（預設 `zh-TW`）
- `--model`：OpenAI 模型（例如 `gpt-5`, `gpt-5-mini`）
- `--skip-convert`：輸入已是 EPUB 時可跳過轉檔
- `--max-chars-per-call`：每次 API 呼叫最大字元數（分批切段）
- `--preview-limit`：僅翻譯前 N 個原子字串（試跑/抽樣），同時輸出 `.preview.epub` 與 `.tsv`
- `--max-workers`：批次並發度（1~25）

#### 範例
- 試跑小樣：
```bash
python mobi_epub_preserve_format_translate.py input.mobi --target zh-TW --model gpt-5 \
  --preview-limit 150 --max-workers 25
```

- 全量輸出（含自我檢查）：
```bash
python mobi_epub_preserve_format_translate.py input.epub --target zh-TW --model gpt-5
```

#### 注意事項
- 需先設定 `OPENAI_API_KEY`。
- 若系統找不到 `ebook-convert`，請安裝 Calibre 或調整腳本內預設路徑。
- 僅翻譯可見文字節點與 `<img alt>`；不改動標籤、屬性或 CSS。
- 自我測試輸出 `<原檔名>.api-selftest.txt` 以確認 API 可用。

---

### 常見問題
- 無法找到 `ebook-convert`：
  - 將 Calibre 安裝後的 `ebook-convert` 放入 PATH，或修改腳本內的預設路徑常數。
- 模型呼叫失敗或速率限制：
  - 程式內含重試與退避；可降低 `--max-workers` 或 `--max-chars-per-call`。

