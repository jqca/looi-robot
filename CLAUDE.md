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
