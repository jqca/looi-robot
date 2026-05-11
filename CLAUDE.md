# LOOI Robot

AI音声対話ロボットアプリ。Flask + Edge TTS + Claude API。
メイン（秘書モード「秋山さん」）とキッズ（「ノア」）の2ページ構成。

## Deploy

- Branch: master
- `git push origin master` で Railway 自動デプロイ
- 本番URL: https://looi-robot-production.up.railway.app
- キッズ: https://looi-robot-production.up.railway.app/kids

## Architecture

### Backend (app.py)
- Flask + Claude API (claude-haiku-4-5)
- Edge TTS（音声合成）: pitch/rate パラメータ対応
- DuckDuckGo + wttr.in（ウェブ検索・天気）
- PostgreSQL（会話履歴・記憶・タスク管理）
- セッションベースのユーザー管理

### Frontend
- Three.js r128 (CDN global build) — 3Dロボット描画
- Web Speech API — 音声認識（continuous mode）
- 両ページとも同じ `RobotRenderer3D` クラスを使用

### API Endpoints
| Path | Method | Description |
|------|--------|-------------|
| `/api/chat` | POST | メイン会話（ウェブ検索付き） |
| `/api/tts` | POST | Edge TTS音声合成（voice/pitch/rate対応） |
| `/api/greet` | GET | メイン挨拶 |
| `/api/news` | GET | Google News RSS取得 |
| `/api/tasks` | GET/POST | タスク管理 |
| `/api/kids/chat` | POST | キッズ会話 |
| `/api/kids/greet` | GET | キッズ挨拶 |
| `/api/kids/name` | POST | ロボット名変更 |

## Characters

### メイン: 秋山さん（秘書AI）
- Voice: ja-JP-KeitaNeural（デフォルト）
- 敬語・結論ファースト・「社長」と呼ぶ
- スリープ/ウェイクワード対応
- ウェイクワード: 起きて/おきて/掟/おはよ/秋山/ねえ/スタート 等
- 5分無操作で自動スリープ → タップまたは声で起動

### キッズ: ノア
- Voice: ja-JP-NanamiNeural（pitch:+40Hz, rate:+15%）
- ひらがな多め・元気・40文字以内
- おやすみモード: 「おやすみ」「ねむい」等で sleep → タップで起動
- 挨拶終了後に自動continuous listening開始

## Design System

### Theme
- Main: Dark mode, mobile-first (max-width: 480px)
- Kids: Sky blue (#87CEEB) background, mobile-first

### Colors (index.html - Main)
| Token | Value | Usage |
|-------|-------|-------|
| bg | #030912 | Page background |
| text | #cce8f4 | Primary text |
| status-muted | #6a8fa8 | Status bar text |
| listening | #ff4488 | Mic active / pink pulse |
| thinking | #aaaaff | Thinking indicator |
| speaking | #88ccee | Speaking indicator |
| ready | #3a6a88 | Ready state |
| sleeping | #1a3a55 | Sleep state |
| input-border | rgba(102,170,255,.2) | Text input border |
| send-btn | rgba(102,170,255,.15) | Send button bg |

### Colors (kids.html)
| Token | Value | Usage |
|-------|-------|-------|
| bg | #87CEEB | Page background (sky blue) |
| listening | #ff4488 | Mic active |
| thinking | #ffaa00 | Thinking indicator |
| speaking | #44ffaa | Speaking indicator |
| ready | #44aaff | Ready state |
| sleeping | #6a5acd | Sleep state (purple) |

### Typography
- Main: `Segoe UI`, fallback `Noto Sans JP`
- Kids: `Hiragino Kaku Gothic ProN`, `Noto Sans JP`, `Meiryo`

### Robot Emotions
idle(blue), happy(green), excited(yellow), thinking(purple), sad(blue-gray), surprised(orange), sleep(dark)

### Component Patterns
- 3D Robot: Three.js full-screen renderer, centered
- Status bar: dot indicator + label
- Text input: rounded pill shape, dark translucent bg
- Start overlay: full-screen tap-to-start with pulse animation

## Key Implementation Notes

### Three.js 注意事項
- Object.assign は rotation/position に使用禁止（Euler/Vector3 が壊れる）
- 正しい方法: `mesh.rotation.z = value` / `mesh.position.set(x,y,z)`

### Voice Interaction Flow
1. ユーザータップ → unlockAudio() → initGreeting()
2. 挨拶TTS完了後 → continuous listening 開始
3. 音声認識 → sendMessage → /api/chat → TTS再生 → 再listening
4. エラー時: 20秒安全タイムアウトで isBusy リセット

### Wake Word Detection (Main)
- スリープ中は別の SpeechRecognition インスタンスで常時監視
- interimResults=true で中間結果も即判定
- onend 後 500ms で自動再起動（途切れを最小化）

## 3D Robot Spatial Dependencies

**変更時は必ずこのリストで影響範囲を確認すること。**

### 基準値（現在）
| パーツ | 値 | 計算根拠 |
|--------|-----|----------|
| body bottom Y | 0.75 | track top と一致（固定） |
| body center Y | 1.114 | 0.75 + bh(0.364) |
| body top Y | 1.478 | 1.114 + bh(0.364) |
| body bottom half-width (bwB) | 0.574 | |
| body top half-width (bwT) | 0.406 | |
| body depth (bDepth) | 0.91 | |
| track center X | 0.77 | bwB + gap(0.04) + trackW/2(0.16) |
| track Y range | 0 ~ 0.75 | |
| track inner edge X | 0.61 | track center - trackW/2 |
| arm shoulder X (ax) | 0.50 | body surface at shoulder height |
| arm shoulder Y | 1.184 | body center + 0.07 |
| upperLen | 0.60 | |
| foreLen | 0.525 | |
| shoulderTiltX | -0.8 | 46° forward（大きいほど前方へ） |
| shoulderTiltZ | ±0.08 | 5° outward（**大きくするとtrack衝突**） |
| elbowBend | -0.8 | 46° additional forward |

### 依存マップ（何を変えたら何を直す？）

| 変更対象 | 影響を受けるパーツ |
|----------|-------------------|
| **bwB/bwT（体幅）** | track center X, arm ax, bumper幅, hatch幅 |
| **bh（体高さ）** | body center Y, body top Y, topPlate位置, handle位置, bumper位置, neck Y, ring Y, eyeUnit Y, arm shoulder Y, bracket Y |
| **bDepth（体奥行き）** | hatch Z, seam Z, rivet Z, hinge Z, vent Z, topPlate奥行き, bumper奥行き |
| **body center Y** | hatch Y, seam Y, rivet Y, hinge Y, vent Y, topPlate Y, handle Y, bumper Y |
| **body top Y** | neck Y(+0.48), ring Y(+0.13), eyeUnit Y(+1.03) |
| **track center X** | track内全パーツ（wheel, belt, sprocket等） |
| **arm ax** | bracket X, shoulderPivot X |
| **upperLen/foreLen** | handGroup counter-rotation計算 |
| **shoulderTiltZ（外側傾き）** | **肘のX座標 → track衝突チェック必須** |
| **upperLen + shoulderTiltZ** | 肘が track inner edge X(0.61) より内側か確認 |
| **rotation.x の符号** | **負=前方(顔側), 正=後方(背面側)** ※π flipに注意 |
| **sideSign と画面左右** | **sideSign=1→画面LEFT, sideSign=-1→画面RIGHT** (π flip) |

### チェックリスト（体サイズ変更時）
1. [ ] bwB/bwT/bh/bDepth変更
2. [ ] body center Y再計算（0.75 + bh）
3. [ ] 体の装飾パーツ全てのY座標更新（hatch, seam, rivet, hinge, vent, topPlate, handle, bumper）
4. [ ] 体の装飾パーツのZ座標更新（bDepth依存のもの）
5. [ ] neck Y更新（body top + 0.48）
6. [ ] ring Y更新（body top + 0.13）
7. [ ] eyeUnit Y更新（body top + 1.03）
8. [ ] **track center X更新**（bwB + 0.04 + trackW/2）
9. [ ] arm ax更新（body surface幅に合わせる）
10. [ ] arm bracket/shoulder Y更新（body center + offset）
11. [ ] camera lookAt Y確認
