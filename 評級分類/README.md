## 評級分類工具

依照片內嵌 XMP 評級（<xmp:Rating> 或 <MicrosoftPhoto:Rating>）篩選 JPG/JPEG。

### 安裝需求
- Python 3.8+

### 基本用法
```bash
python script.py --src /path/to/source --dest /path/to/output
```

### 參數說明
- `--src, -s`：來源根目錄（必填）
- `--dest, -d`：輸出根目錄（預設：當前目錄下 `rated_images/`）
- `--min-rating`：最低評級（含），預設 `1`
- `--op`：`copy` 或 `move`，預設 `copy`
- `--group-by-rating`：依評級分子資料夾（預設啟用）
- `--no-group-by-rating`：不依評級分子資料夾，全放同一層
- `--preserve-structure`：保留來源目錄結構到輸出目錄
- `--on-conflict`：同名檔案衝突策略，`skip`/`overwrite`/`rename`（預設 `rename`）
- `--workers`：掃描與解析評級時的執行緒數（預設 `8`）

### 範例
- 依評級分子資料夾、複製到輸出目錄：
```bash
python script.py -s /Volumes/vic88/100EOSR7 -d ./rated_images --min-rating 2 --op copy --group-by-rating
```

- 不分評級子資料夾、保留原始子結構、同名改名避免覆蓋：
```bash
python script.py -s /Volumes/vic88/100EOSR7 -d ./out --no-group-by-rating --preserve-structure --on-conflict rename
```

- 搬移（而非複製）符合評級的影像：
```bash
python script.py -s /src -d /dst --op move
```

### 支援格式
- `.jpg`, `.jpeg`

### 輸出與摘要
程式會顯示：
- 掃描進度：已處理/總數、累計符合評級數
- 傳輸進度：已處理/總數、已完成數
- 最終摘要：來源、輸出、候選檔數、符合評級數與各評級統計
- 錯誤摘要：讀取/解析錯誤與傳輸錯誤（各顯示前 10 筆）

### 注意事項
- XMP 解析採用簡單字串搜尋。
- `--on-conflict overwrite` 會嘗試覆蓋既有檔案，請謹慎使用。


