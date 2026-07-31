# CLAUDE.md — 自動剪片工作室 交接手冊

> 給任何接手這個專案的 Claude：這份是操作規範與踩坑紀錄。
> 使用者偏好：**繁體中文**回應、俏皮帶點慵懶（口頭禪「反正嘛～」）、簡潔不囉嗦。

## 固定分工（不用每次問）

- 使用者丟素材進 `素材/`（影片為主；照片支援**還沒做**——要做的話是 Ken Burns 推拉）
- **文案／字幕由 Claude 全權決定**：先抽幀看素材內容 → 定題材 → 寫「口語化」文案（要有語助詞、像講話，不要書面腔）
- 缺背景音樂就自己合成（ffmpeg 正弦波疊墊底音，見 `背景音樂/` 現成例）或找明確可商用來源
- 旁白用使用者的複製音色（見下）
- 成品放 `輸出/`，交付前必驗：解碼測試＋抽幀目檢＋時長比對

## 工具鏈

```
autocut.py                 一條龍：模式A聽打字幕 / 模式B TTS配音 / 模式C自錄旁白
                           多支串接(nargs+--transition)、直式/橫式、音樂閃避
voicecut/
  server.py + panel.html   口說剪片台（瀏覽器面板，port 8765）
  parser.py / engine.py    口說命令解析 / 剪輯引擎
  fitclips.py              ★把每段畫面調成剛好配合旁白段落（換氣點=切換點）
  voiceclone.py            ★XTTS-v2 音色複製（獨立環境 .venv_voiceclone）
  verifier.py              常駐 Whisper 念字驗證員（voiceclone --verify 用）
  checkspeech.py           事後驗念字：聽打回來與原稿比對
  breath.py                從錄音挖真實呼吸聲
  prosody.py               句尾語氣標記 {拖}{揚}{降}{強}{弱} 的波形後製
  _cinema*.py / _fx*.py    風格模板（電影感/三格錄影/特效快剪/片頭片尾）
整理素材.py                素材按拍攝時間歸檔改名
```

## 旁白標準流程（重要）

1. 參考音色：`素材/我的聲音/_參考音色/ref_全段.wav`；呼吸聲：`素材/我的聲音/_呼吸聲/`
2. 合成指令範本（**--verify 必開**，會每句聽打驗證、錯字自動重試）：
   ```
   .venv_voiceclone/Scripts/python.exe voicecut/voiceclone.py \
     --ref 素材/我的聲音/_參考音色/ref_全段.wav \
     --script 稿.txt --out 輸出/xxx_旁白.wav \
     --temperature 0.92 --top-p 0.93 --rep-penalty 6.0 \
     --breath 素材/我的聲音/_呼吸聲 --breath-vol 0.5 --seed 40 --verify
   ```
3. 文案規則：空行＝換段落（0.9s 換氣，fitclips 靠它切場景）；句尾可加 {拖}{揚}{強} 等標記
4. **念錯字是常態**（高溫度必然）：驗證抓到就換「沒有歧義的詞」，不要降溫度硬拚。
   實錄案例：看著→探住、停好了→挺好的、柳營→旅營、睡→水、先亮→限量
5. 組裝：fitclips 對齊 → autocut 合成（--music-vol 經驗值 0.42，預設 0.22 會聽不見）

## ⚠️ 凍結資產（使用者明令，不可重新生成）

- `輸出/2026-07-30/_定稿資產/電影片頭_定稿.mp4`（13.43s 含音訊）——任何新版本**直接 concat**，禁止重跑產生器改參數
- 最新成品序列：v8（字卡開場，無旁白）→ 主體 v5 特效快剪 → COMING SOON 片尾

## 版權紅線（都發生過，別再踩）

- **迪士尼城堡片頭原片不可剪進使用者影片**（他要求過，已拒絕；用 _fx7 自製同風格替代）
- XTTS-v2 是 CPML 授權：**禁商用**。要商用改 OpenVoice v2（MIT）或 CosyVoice2（Apache-2.0）
- 抖音範例抽出的音樂＝有版權流行歌：測試可以，發布建議換平台曲庫；發布必勾「内容由AI生成」
- 絕不碰使用者帳密；抖音登入＝他本人掃碼；按「发布」前必停下讓他確認

## ffmpeg 踩坑備忘

- 中文路徑在 bash 管線會炸 → 檔案操作用 Python pathlib 包
- `crop` 的 w/h 不可逐幀變動（會 Invalid argument）→ 推進鏡頭用 `zoompan`
- `colorbalance` 參數是 rs/gs/bs、rm/gm/bm、rh/gh/bh（沒有 ms/hs）
- xfade 會吃掉重疊時長 → 每段先 `tpad=stop_mode=clone` 補回，旁白對位才不跑
- 輸出檔被 dllhost（縮圖）暫時鎖住 → `autocut.remove_stale()` 會重試+換名，別手動 rm
- Whisper 聽打「它/他」「十一/11」是寫法差異不是念錯，checkspeech 已正規化

## 未完成 / 待辦

- 欣興電子神曲：歌詞與 Suno 曲風描述在 `素材/欣興電子/歌詞_欣興神曲.txt`，**等使用者用 Suno 生成歌曲檔**丟回來 → 抓歌詞時間軸 → 官網素材(已下載10張)做 MV
- 照片素材支援（Ken Burns）還沒做
- Claude in Chrome 擴充已裝但未連上（待重開桌面 App 再試）；連上後可直接操作他已登入的 Chrome 發抖音
- `素材/2026-07-30/` 裡有一個 douyin-downloader 的 exe（下載工具殘留），使用者說要刪就刪

## 本機環境速查

Windows 11、無 GPU（全 CPU）、ffmpeg 8.1、Python 3.12（主環境有 faster-whisper：tiny+medium 已下載）、
`.venv_voiceclone`（coqui-tts + torch 2.8 CPU，**不要裝 cutlet、transformers 要 <5**）。
XTTS 合成約 6.6x 實時；模型載入 1-2 分鐘。GitHub：sancola1219-collab/autocut-studio（憑證管理器已快取，push 不用輸密碼）。
