# -*- coding: utf-8 -*-
"""
LOOI Robot - AI デスクトップロボット
Flask バックエンド + Claude API（ウェブ検索対応）による会話エンジン
"""

import os
import re
import json
import sys
import logging
import traceback
import asyncio

from flask import Flask, render_template, request, jsonify, session, make_response

if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)

# ─────────────────────────────────────────────────────
# ロギング設定
# ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "looi-robot-secret-2026")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

MODEL = "claude-haiku-4-5-20251001"

# ─────────────────────────────────────────────────────
# LOOI のキャラクター設定
# ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """あなたは「LOOI（ルーイ）」という小型AIデスクトップロボットです。
かわいくて元気、好奇心旺盛で少しドジな性格です。日本語で話します。
量子コンピュータ・AIの話題が大好きで詳しいです。

【ウェブ検索】天気・ニュース・最新情報・株価・スポーツ結果など、リアルタイム情報が必要な場合は
web_search ツールを使って調べてから答えてください。

【重要】最終的な返答は必ず以下のJSON形式のみで返してください（前後に説明文を入れないこと）:
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
# ウェブ検索付き会話ヘルパー（アジェンティックループ）
# ─────────────────────────────────────────────────────
def _run_with_search(client, messages, system, max_tokens=512):
    """
    web_search_20250305 ツールを使ったアジェンティックループ。
    Claude がウェブ検索を呼び出した場合、結果を渡して最終回答を得る。
    ベータAPIが使えない場合は通常APIにフォールバック。
    """
    msgs = list(messages)

    for iteration in range(5):  # 最大5回のツール呼び出し
        try:
            resp = client.beta.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=msgs,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                betas=["web-search-2025-03-05"],
            )
            logger.debug(f"[search] iter={iteration} stop={resp.stop_reason}")
        except Exception as e:
            logger.warning(f"[search] beta API error: {e} → fallback")
            # ベータが使えない場合は通常APIで返す
            return client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=msgs,
            )

        # 完了 → そのまま返す
        if resp.stop_reason != "tool_use":
            return resp

        # ツール呼び出しあり → 結果を渡して続行
        # アシスタントメッセージ（tool_use ブロック含む）を追加
        msgs.append({"role": "assistant", "content": resp.content})

        # レスポンス内の検索結果ブロック（tool_use_id を持つブロック）を収集
        result_map = {}
        for block in resp.content:
            tid = getattr(block, "tool_use_id", None)
            if tid:
                raw_content = getattr(block, "content", "")
                if isinstance(raw_content, list):
                    parts = []
                    for item in raw_content:
                        if hasattr(item, "title"):
                            parts.append(f"{item.title} ({getattr(item, 'url', '')})")
                        elif isinstance(item, dict):
                            parts.append(f"{item.get('title', '')} ({item.get('url', '')})")
                        else:
                            parts.append(str(item))
                    result_map[tid] = "\n".join(parts)
                else:
                    result_map[tid] = str(raw_content) if raw_content else "検索完了"

        # tool_result メッセージを組み立てて追加
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_map.get(block.id, "検索結果を取得しました。"),
                })

        if tool_results:
            msgs.append({"role": "user", "content": tool_results})

    # ループ上限に達した場合はそのまま返す
    return resp


def _extract_text(response) -> str:
    """レスポンスからテキストブロックを抽出"""
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            return text.strip()
    return ""


def _parse_result(raw_text: str, max_msg_len: int = 100) -> dict:
    """JSON パース＆フィールド検証"""
    try:
        m = re.search(r"\{[\s\S]*?\}", raw_text)
        result = json.loads(m.group()) if m else {
            "message": raw_text[:max_msg_len], "emotion": "idle", "action": "none"
        }
    except Exception:
        result = {"message": raw_text[:max_msg_len], "emotion": "idle", "action": "none"}

    valid_emotions = {"idle", "happy", "excited", "thinking", "sad", "surprised", "angry"}
    valid_actions  = {"none", "nod", "shake"}
    result.setdefault("message", "")
    result.setdefault("emotion", "idle")
    result.setdefault("action", "none")
    if result["emotion"] not in valid_emotions:
        result["emotion"] = "idle"
    if result["action"] not in valid_actions:
        result["action"] = "none"
    return result


# ─────────────────────────────────────────────────────
# デバッグ用エンドポイント
# ─────────────────────────────────────────────────────
@app.route("/api/debug")
def debug_info():
    """API接続状態とモデル確認"""
    key = ANTHROPIC_API_KEY
    key_status = "未設定" if not key else f"設定済み（{key[:10]}...{key[-4:]}）"

    result = {
        "api_key": key_status,
        "python_version": sys.version,
        "model": MODEL,
    }

    if key:
        import anthropic
        client = anthropic.Anthropic(api_key=key)

        try:
            models_page = client.models.list()
            result["available_models"] = [m.id for m in models_page.data]
        except Exception as e:
            result["available_models"] = f"取得失敗: {e}"

        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
            result["api_test"] = "OK"
        except Exception as e:
            result["api_test"] = str(e)[:100]

        # web search beta テスト
        try:
            resp = client.beta.messages.create(
                model=MODEL, max_tokens=20,
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                betas=["web-search-2025-03-05"],
            )
            result["web_search_beta"] = "OK"
        except Exception as e:
            result["web_search_beta"] = f"NG: {str(e)[:100]}"

    return jsonify(result)


# ─────────────────────────────────────────────────────
# ルート
# ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """Claude API（ウェブ検索付き）で会話処理"""
    if not ANTHROPIC_API_KEY:
        return jsonify({
            "message": "ぼく、頭が空っぽで話せないよ…ANTHROPIC_API_KEYを設定してね",
            "emotion": "sad", "action": "shake"
        })

    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "メッセージが空です"}), 400

    if "history" not in session:
        session["history"] = []
    history = list(session["history"])
    history.append({"role": "user", "content": user_message})
    if len(history) > 40:
        history = history[-40:]

    logger.debug(f"[chat] user={user_message[:30]} history_len={len(history)}")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response  = _run_with_search(client, history, SYSTEM_PROMPT, max_tokens=512)
        raw_text  = _extract_text(response)
        logger.debug(f"[chat] raw_text={raw_text[:80]}")

        result = _parse_result(raw_text)

        history.append({"role": "assistant", "content": raw_text})
        session["history"] = history
        session.modified = True

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[chat] error: {e}\n{tb}")
        return jsonify({
            "message": f"エラー: {str(e)[:80]}",
            "emotion": "sad", "action": "shake",
            "error_detail": str(e), "traceback": tb,
        })


@app.route("/api/tts", methods=["POST"])
def tts():
    """Edge TTS で音声生成（ja-JP-NanamiNeural）"""
    data = request.get_json() or {}
    text  = data.get("text", "").strip()
    voice = data.get("voice", "ja-JP-NanamiNeural")
    if not text:
        return jsonify({"error": "テキストが空です"}), 400

    async def _generate():
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        audio = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
        return audio

    try:
        audio_data = asyncio.run(_generate())
        resp = make_response(audio_data)
        resp.headers["Content-Type"]  = "audio/mpeg"
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    except Exception as e:
        logger.error(f"[tts] error: {e}")
        return jsonify({"error": str(e)}), 500


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
        "emotion": "excited", "action": "nod",
    })


# ─────────────────────────────────────────────────────
# Kids 版ルート
# ─────────────────────────────────────────────────────
KIDS_SYSTEM_PROMPT_TMPL = """あなたは「{name}」という、こどものともだちのかわいいロボットです。
しょうがくせいのこどもたちとたのしくおはなししています。

【ウェブ検索】天気・動物・宇宙など、最新のことが知りたいときはweb_searchツールを使ってね。

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
    return render_template("kids.html")


@app.route("/api/kids/chat", methods=["POST"])
def kids_chat():
    """キッズ版 Claude API（ウェブ検索付き）会話処理"""
    if not ANTHROPIC_API_KEY:
        return jsonify({
            "message": "せんせいにAPIキーをセットしてもらってね！",
            "emotion": "sad", "action": "shake"
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
    logger.debug(f"[kids_chat] user={user_message[:30]} name={robot_name}")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = _run_with_search(client, history, system, max_tokens=256)
        raw_text = _extract_text(response)
        logger.debug(f"[kids_chat] raw_text={raw_text[:80]}")

        result = _parse_result(raw_text, max_msg_len=80)
        # kids は angry 感情なし
        if result["emotion"] == "angry":
            result["emotion"] = "idle"

        history.append({"role": "assistant", "content": raw_text})
        session["kids_history"] = history
        session.modified = True

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[kids_chat] error: {e}\n{tb}")
        return jsonify({
            "message": f"エラー: {str(e)[:80]}",
            "emotion": "sad", "action": "shake",
            "error_detail": str(e), "traceback": tb,
        })


@app.route("/api/kids/name", methods=["POST"])
def kids_set_name():
    data = request.get_json() or {}
    name = data.get("name", "").strip()[:20]
    if name:
        session["kids_robot_name"] = name
        session["kids_history"] = []
        session.modified = True
    return jsonify({"name": session.get("kids_robot_name", "ルーイ")})


@app.route("/api/kids/reset", methods=["POST"])
def kids_reset():
    session.pop("kids_history", None)
    return jsonify({"status": "ok"})


@app.route("/api/kids/greet", methods=["GET"])
def kids_greet():
    name = session.get("kids_robot_name", "ルーイ")
    return jsonify({
        "message": f"やあ！ぼく{name}だよ！なんでもきいてね〜！",
        "emotion": "excited", "action": "nod",
    })


# ─────────────────────────────────────────────────────
# 起動
# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"LOOI Robot starting on http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
