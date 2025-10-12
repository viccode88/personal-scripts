## 電子書轉換與翻譯工具（trans）

單一指令：`convert_and_translate.py`

- 子指令 `convert`：批量將 `.mobi`/`.azw3` 轉為 `.epub`（需要 Calibre 的 `ebook-convert`）。
- 子指令 `translate`：翻譯 `.mobi`/`.epub` 的可見文字並保持排版/圖片/CSS（需要 OpenAI API）。

### 安裝需求
- Python 3.9+
- 套件：`requests`, `beautifulsoup4`, `lxml`, `openai`
- 轉檔：需安裝 Calibre 並提供 `ebook-convert` 指令於 PATH（或使用預設安裝路徑）
- 翻譯：需設定環境變數 `OPENAI_API_KEY`

---

### 子指令：convert（批量轉 EPUB）

從指定根目錄遞迴搜尋 `.mobi`/`.azw3`，以 Calibre 的 `ebook-convert` 轉為 `.epub`，具備「已存在且較新則跳過」與多線程併發。

#### 基本用法
```bash
python convert_and_translate.py convert --base-dir /path/to/root \
  [--exts ".mobi,.azw3"] [--workers 8]
```

#### 參數
- `--base-dir`：掃描根目錄（必填）
- `--exts`：來源副檔名，逗號分隔，可含或不含點（預設 `.mobi,.azw3`）
- `--workers`：併發度（預設 8）

---

### 子指令：translate

將 `.mobi` 先轉為 `.epub`（或直接 `.epub`），抽取 XHTML/XML 可見文字與 `<img alt>`，呼叫 OpenAI 進行翻譯並回填，最後重新封裝。

#### 基本用法
```bash
python convert_and_translate.py translate INPUT(.mobi|.epub) \
  --target zh-TW --model gpt-5 [--skip-convert] \
  [--max-chars-per-call 3500] [--preview-limit 0] [--max-workers 10]
```

#### 參數
- `input`：輸入檔，支援 `.mobi` 或 `.epub`
- `--target`：目標語言（預設 `zh-TW`）
- `--model`：OpenAI 模型（例如 `gpt-5`, `gpt-5-mini`）
- `--skip-convert`：輸入已是 EPUB 時可跳過轉檔（若輸入是 `.mobi` 則不得使用）
- `--max-chars-per-call`：每次 API 呼叫最大字元數（分批切段）
- `--preview-limit`：僅翻譯前 N 個原子字串（試跑/抽樣），同時輸出 `.preview.epub` 與 `.tsv`
- `--max-workers`：批次並發度（1~25）

#### 範例
- 試跑：
```bash
python convert_and_translate.py translate input.mobi --target zh-TW --model gpt-5 \
  --preview-limit 150 --max-workers 25
```

- 全量輸出（含自我檢查）：
```bash
python convert_and_translate.py translate input.epub --target zh-TW --model gpt-5
```

#### 注意事項
- 需先設定 `OPENAI_API_KEY`。
- 若系統找不到 `ebook-convert`，請安裝 Calibre 或調整腳本內預設路徑。
- 僅翻譯可見文字節點與 `<img alt>`；不改動標籤、屬性或 CSS。
- 自我測試輸出 `<原檔名>.api-selftest.txt` 以確認 API 可用。

---

### 常見問題
- 模型呼叫失敗或速率限制：
  - 程式內含重試與退避；可降低 `--max-workers` 或 `--max-chars-per-call`。

