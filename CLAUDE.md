# LOOI Robot

AI音声対話ロボットアプリ。Flask + Edge TTS + Claude API。
メイン（秘書モード）とキッズの2ページ構成。

## Deploy

- Branch: master
- `git push origin master` で Railway 自動デプロイ

## Design System

### Theme
- Dark mode, mobile-first (max-width: 480px)

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
| --bg | #06091a | Page background |
| --bg2 | #0d1433 | Secondary bg |
| --card | #111830 | Card background |
| --text | #e8f0ff | Primary text |
| --muted | #6080c0 | Muted text |
| --radius | 20px | Border radius |

### Typography
- Main: `Segoe UI`, fallback `Noto Sans JP`
- Kids: `Hiragino Kaku Gothic ProN`, `Noto Sans JP`, `Meiryo`

### Robot Emotions
idle(blue), happy(green), excited(yellow), thinking(purple), sad(blue-gray), surprised(orange), sleep(dark)

### Component Patterns
- Canvas: circular robot face, centered
- Status bar: dot indicator + label
- Text input: rounded pill shape, dark translucent bg
- Start overlay: full-screen tap-to-start with pulse animation

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
| arm shoulder X (ax) | 0.50 | body surface at shoulder height |
| arm shoulder Y | 1.184 | body center + 0.07 |
| upperLen | 0.60 | |
| foreLen | 0.525 | |

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
