# V4 Stage 2 重現

正式研究使用 `outputs/v4/stage2_verified/`；`outputs/v4/stage2/` 是已隔離的首次執行，不能作為研究結論。三個家族使用相同窗口與成本，各有 36 組固定候選；修正僅處理第 24 天取消資格的報酬分類。

## 核對既有證據

在原執行工作目錄中，以下指令只讀取封存資料並輸出核對結果：

```bash
.venv/bin/python scripts/v4_stage2_verify.py --output outputs/v4/stage2_verified --workers 8
.venv/bin/python -m unittest discover -s tests -q
```

來源清單保留原執行目錄的絕對路徑。若在另一個 checkout 重現，先按下一節建立新的離線資料清單；不要直接改寫封存清單。大型 `features.pkl` 不納入 Git，由凍結日線快照與程式重新建立。

## 在新目錄完整重跑

先安裝專案的 `requirements.txt` 與 `requirements-yahoo.txt`。原執行的 Python、NumPy、pandas、SciPy 版本記錄在 [environment.json](../outputs/v4/stage2_verified/environment.json)。

以下準備步驟複製已驗證的官方原始回應，建立獨立設定；名稱已存在時會停止，避免覆寫：

```bash
.venv/bin/python - <<'PY'
import json
import shutil
from pathlib import Path

study_path = Path('config/v4_replay_study.json')
cache = Path('outputs/v4/stage2_replay_data')
if study_path.exists() or cache.exists():
    raise SystemExit('請選擇尚未使用的 replay 設定與目錄名稱')
study = json.loads(Path('config/v4_stage2_study.json').read_text())
study['execution_data'] = str(cache / 'execution_data.csv')
study['episode_registry'] = str(cache / 'episodes.json')
cache.mkdir(parents=True)
shutil.copytree('outputs/v4/stage2_data/official_raw', cache / 'official_raw')
study_path.write_text(json.dumps(study, indent=2) + '\n')
PY

.venv/bin/python scripts/v4_stage2_data.py --study config/v4_replay_study.json --offline --workers 1
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python scripts/v4_evaluate.py --study config/v4_replay_study.json --output outputs/v4/stage2_replay --workers 8
```

最後稽核並產生獨立報告。若驗證指令失敗，停止後續報告步驟，保留錯誤與原始結果。

```bash
.venv/bin/python scripts/v4_stage2_verify.py --output outputs/v4/stage2_replay --workers 8 > outputs/v4/stage2_replay/verification.json
.venv/bin/python scripts/v4_stage2_report.py --output outputs/v4/stage2_replay --reports outputs/v4/stage2_replay/reports --final-config outputs/v4/stage2_replay/v4_final.json
```

研究指標與逐日帳本可作逐檔比對；執行時間、目錄、事件鏈與 provenance 清單會隨新執行而改變。官方來源不足時，程式會在搜尋前停止，不以 Open／Close 代替官方均價。
