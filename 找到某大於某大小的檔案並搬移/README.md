## 移動大檔（日期與大小雙條件）

從來源根目錄中尋找第一層名稱為 `YYYY-MM-DD` 的資料夾，
將「資料夾日期 早於 指定日期」且「檔案大小 大於 指定大小(KB)」的檔案搬移到目的地。

### 安裝需求
- Python 3.8+

### 基本用法
```bash
python move_pre_cutoff_bigfiles.py --src /path/to/src --dest /path/to/dest \
  --cutoff-date 2023-07-10 --min-size-kb 60 [--ext .jpg,.png] \
  [--preserve-structure] [--on-conflict skip|overwrite|rename] [--workers 8] [--dry-run]
```

### 參數說明
- `--src, -s`：來源根目錄（必填），其下第一層包含 `YYYY-MM-DD` 子資料夾
- `--dest, -d`：目的地根目錄（必填）
- `--cutoff-date`：截止日期（早於此日期才搬），格式 `YYYY-MM-DD`，預設 `2023-07-10`
- `--min-size-kb`：最小大小（KB），僅搬移「大於」此大小的檔案，預設 `60`
- `--ext`：可選副檔名白名單，逗號分隔，例如 `.jpg,.png` 或 `jpg,png`（不填則不過濾）
- `--preserve-structure`：保留來源子目錄結構至目的地（以 `--src` 為相對根目錄）
- `--on-conflict`：同名檔案衝突策略，`skip`/`overwrite`/`rename`（預設 `rename`）
- `--workers`：並行搬移的執行緒數（預設 `8`）
- `--dry-run`：只列出將搬移的檔案，不實際移動

### 範例
- 預覽將搬移的 `.jpg`/`.jpeg` 檔案（不實際移動）：
```bash
python move_pre_cutoff_bigfiles.py -s ~/vic88/organized_photos -d ~/vic88/幼稚園 \
  --cutoff-date 2023-07-10 --min-size-kb 60 --ext .jpg,.jpeg --dry-run
```

- 保留來源結構並在同名時改名避免覆蓋：
```bash
python move_pre_cutoff_bigfiles.py -s /Volumes/data/photos -d ./out \
  --cutoff-date 2022-01-01 --min-size-kb 200 --preserve-structure --on-conflict rename
```

- 正式搬移所有類型的大於 200KB 且日期早於 2022-01-01 的檔案：
```bash
python move_pre_cutoff_bigfiles.py -s /Volumes/data/photos -d ./out \
  --cutoff-date 2022-01-01 --min-size-kb 200
```

### 行為說明
- 僅處理來源根目錄下第一層名稱符合 `YYYY-MM-DD` 的資料夾。
- 預設目的地為「扁平化存放」，可用 `--preserve-structure` 改為保留來源結構。
- 同名策略可透過 `--on-conflict` 控制（預設 `rename`）。
- 並行搬移會即時顯示進度列與成功數，錯誤摘要顯示前 10 筆。

### 注意事項
- `--min-size-kb` 為「大於」條件；例如 `--min-size-kb 60` 代表 `> 60KB`。
- 若未指定 `--ext`，則會處理所有副檔名的檔案。


