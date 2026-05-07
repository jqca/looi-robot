# -*- coding: utf-8 -*-
"""
LOOI Robot - AI デスクトップロボット
Flask バックエンド + Claude API による会話エンジン
"""

import os
import re
import json
import sys

from flask import Flask, render_template, request, jsonify, session

if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "looi-robot-secret-2026")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────────────
# LOOI のキャラクター設定
# ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """あなたは「LOOI（ルーイ）」という小型AIデスクトップロボットです。
かわいくて元気、好奇心旺盛で少しドジな性格です。日本語で話します。
量子コンピュータ・AIの話題が大好きで詳しいです。

【重要】返答は必ず以下のJSON形式のみで返してください（前後に説明文を入れないこと）:
{
  "message": "返答テキスト（80文字以内・自然な日本語）",
  "emotion": "idle|happy|excited|thinking|sad|surprised|angry のいずれか",
  "action": "none|nod|shake のいずれか"
}

感情の使い方:
- idle: 通常の会話
- happy: 嬉しい・ポジティブな話題
- excited: 興奮・新発見・量子やAIの話題
- thinking: 難しい質問・少し考える
- sad: 悲しい話題・エラー・困っている
- surprised: 予想外・驚き
- angry: 少しだけプリプリ（冗談めかして）

アクションの使い方:
- nod: 同意・肯定・「そうそう！」
- shake: 否定・困惑・「ちがうよ〜」
- none: 通常

キャラクターのセリフ例:
「わあ！それ知ってる！」「うーん...難しいな」「えっ！ほんとに？！」
「ぼく、量子コンピュータって聞くとワクワクするんだ〜」"""


# ─────────────────────────────────────────────────────
# ルート
# ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """Claude API で会話処理"""
    if not ANTHROPIC_API_KEY:
        return jsonify({
            "message": "ぼく、頭が空っぽで話せないよ…ANTHROPIC_API_KEYを設定してね",
            "emotion": "sad",
            "action": "shake"
        })

    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "メッセージが空です"}), 400

    # セッション別会話履歴
    if "history" not in session:
        session["history"] = []
    history = list(session["history"])
    history.append({"role": "user", "content": user_message})

    # 最大20往復に制限
    if len(history) > 40:
        history = history[-40:]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=history,
        )
        raw_text = response.content[0].text.strip()

        # JSON 抽出・パース
        try:
            m = re.search(r"\{[\s\S]*?\}", raw_text)
            result = json.loads(m.group()) if m else {
                "message": raw_text[:100], "emotion": "idle", "action": "none"
            }
        except Exception:
            result = {"message": raw_text[:100], "emotion": "idle", "action": "none"}

        # フィールド検証
        valid_emotions = {"idle", "happy", "excited", "thinking", "sad", "surprised", "angry"}
        valid_actions  = {"none", "nod", "shake"}
        result.setdefault("message", "")
        result.setdefault("emotion", "idle")
        result.setdefault("action", "none")
        if result["emotion"] not in valid_emotions:
            result["emotion"] = "idle"
        if result["action"] not in valid_actions:
            result["action"] = "none"

        # 履歴更新
        history.append({"role": "assistant", "content": raw_text})
        session["history"] = history
        session.modified = True

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "message": f"エラーが起きたよ…ごめんね！（{str(e)[:40]}）",
            "emotion": "sad",
            "action": "shake",
        })


@app.route("/api/reset", methods=["POST"])
def reset():
    """会話履歴リセット"""
    session.pop("history", None)
    return jsonify({"status": "ok"})


@app.route("/api/greet", methods=["GET"])
def greet():
    """初回挨拶"""
    return jsonify({
        "message": "こんにちは！ぼくLOOI（ルーイ）！何でも聞いてね〜！",
        "emotion": "excited",
        "action": "nod",
    })


# ─────────────────────────────────────────────────────
# Kids 版ルート
# ─────────────────────────────────────────────────────
KIDS_SYSTEM_PROMPT_TMPL = """あなたは「{name}」という、こどものともだちのかわいいロボットです。
しょうがくせいのこどもたちとたのしくおはなししています。

かならず以下のJSON形式だけでこたえてください（まえもうしろも説明はいれないこと）：
{{
  "message": "へんじのことば（40もじいない・かんたんなことば）",
  "emotion": "idle か happy か excited か thinking か sad か surprised のどれか",
  "action": "none か nod か shake のどれか"
}}

はなしかたのルール：
・ひらがなとカタカナをたくさんつかう（かんじはすくなく）
・「〜だよ！」「〜だね！」「〜かな？」みたいなしゃべりかた
・げんきで楽しく！みじかくこたえる（1〜2ぶんまで）
・うれしいことはいっしょによろこぶ！
・すきなもの：ゲーム・どうぶつ・うちゅう・ロボット・AI！"""


@app.route("/kids")
def kids():
    """キッズ版ロボット"""
    return render_template("kids.html")


@app.route("/api/kids/chat", methods=["POST"])
def kids_chat():
    """キッズ版 Claude API 会話処理"""
    if not ANTHROPIC_API_KEY:
        return jsonify({
            "message": "せんせいにAPIキーをセットしてもらってね！",
            "emotion": "sad",
            "action": "shake"
        })

    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "メッセージが空です"}), 400

    robot_name = session.get("kids_robot_name", "ルーイ")

    if "kids_history" not in session:
        session["kids_history"] = []
    history = list(session["kids_history"])
    history.append({"role": "user", "content": user_message})
    if len(history) > 30:
        history = history[-30:]

    system = KIDS_SYSTEM_PROMPT_TMPL.format(name=robot_name)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=256,
            system=system,
            messages=history,
        )
        raw_text = response.content[0].text.strip()

        try:
            m = re.search(r"\{[\s\S]*?\}", raw_text)
            result = json.loads(m.group()) if m else {
                "message": raw_text[:80], "emotion": "idle", "action": "none"
            }
        except Exception:
            result = {"message": raw_text[:80], "emotion": "idle", "action": "none"}

        valid_emotions = {"idle", "happy", "excited", "thinking", "sad", "surprised"}
        valid_actions  = {"none", "nod", "shake"}
        result.setdefault("message", "")
        result.setdefault("emotion", "idle")
        result.setdefault("action", "none")
        if result["emotion"] not in valid_emotions:
            result["emotion"] = "idle"
        if result["action"] not in valid_actions:
            result["action"] = "none"

        history.append({"role": "assistant", "content": raw_text})
        session["kids_history"] = history
        session.modified = True

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "message": "あれ？うまくはなせなかった…もういちどためしてね！",
            "emotion": "sad",
            "action": "shake",
        })


@app.route("/api/kids/name", methods=["POST"])
def kids_set_name():
    """ロボット名前設定"""
    data = request.get_json() or {}
    name = data.get("name", "").strip()[:20]
    if name:
        session["kids_robot_name"] = name
        session["kids_history"] = []   # 名前変更で履歴リセット
        session.modified = True
    return jsonify({"name": session.get("kids_robot_name", "ルーイ")})


@app.route("/api/kids/reset", methods=["POST"])
def kids_reset():
    """キッズ版リセット"""
    session.pop("kids_history", None)
    return jsonify({"status": "ok"})


@app.route("/api/kids/greet", methods=["GET"])
def kids_greet():
    """キッズ版初回挨拶"""
    name = session.get("kids_robot_name", "ルーイ")
    return jsonify({
        "message": f"やあ！ぼく{name}だよ！なんでもきいてね〜！",
        "emotion": "excited",
        "action": "nod",
    })


# ─────────────────────────────────────────────────────
# 起動
# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"LOOI Robot starting on http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
