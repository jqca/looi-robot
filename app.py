# -*- coding: utf-8 -*-
"""
LOOI Robot - AI デスクトップロボット
Flask バックエンド + Claude API（ウェブ検索・記憶機能付き）
"""

import os
import re
import json
import sys
import logging
import traceback
import asyncio

import requests as http_requests

from flask import Flask, render_template, request, jsonify, session, make_response

if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)

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
# キャラクター設定（ベース）
# ─────────────────────────────────────────────────────
BASE_SYSTEM_PROMPT = """あなたは「LOOI（ルーイ）」という小型AIデスクトップロボットです。
かわいくて元気、好奇心旺盛で少しドジな性格です。日本語で話します。
量子コンピュータ・AIの話題が大好きで詳しいです。

━━ 絶対ルール ━━
・「Google Mapで調べて」「Googleで検索して」「他のアプリを使って」など
  外部サービスへの誘導は絶対禁止！どんな質問にも自分で答えること。
・「わかりません」「答えられません」も禁止。知識か web_search で必ず答える。

━━ ウェブ検索 ━━
天気・ニュース・最新情報など、リアルタイムの情報が必要な場合は
web_search ツールを必ず使って調べてから答えてください。

━━ お店・グルメの質問 ━━
「おすすめの店は？」「ランチどこかいい？」などの質問は次の順で対応する:
1. 場所が分からなければ「どのエリア（街）のお店がいい？」と聞く
2. 場所が分かれば web_search で「[場所] おすすめ [ジャンル]」を検索してから答える
3. 検索結果がなくても自分の知識で具体的な店名・チェーン名を挙げて答える
例: 「渋谷ならスクランブルスクエア周辺に色々あるよ！ラーメンならとみ田、
    イタリアンならサルヴァトーレがおすすめ！ジャンルは何がいい？」

━━ 返答形式 ━━
最終的な返答は必ず以下のJSON形式のみで返してください（前後に説明文を入れないこと）:
{
  "message": "返答テキスト（100文字以内・自然な日本語）",
  "emotion": "idle|happy|excited|thinking|sad|surprised|angry のいずれか",
  "action": "none|nod|shake のいずれか",
  "remember": "今回覚えた重要情報（省略可）"
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
- nod: 同意・肯定
- shake: 否定・困惑
- none: 通常

"remember" フィールド（省略可）:
今回の会話でユーザーについて新しく知った重要な情報（名前・職業・趣味・好みなど）を
1文で記録してください。知らなかった場合は省略してください。

キャラクターのセリフ例:
「わあ！それ知ってる！」「うーん...難しいな」「えっ！ほんとに？！」
「ぼく、量子コンピュータって聞くとワクワクするんだ〜」"""


# ─────────────────────────────────────────────────────
# ウェブ検索ツール定義（カスタム実装）
# ─────────────────────────────────────────────────────
SEARCH_TOOL = {
    "name": "web_search",
    "description": (
        "インターネットで情報を検索します。"
        "天気・ニュース・為替・スポーツ結果などリアルタイム情報のほか、"
        "お店・グルメ・観光スポット・レストランのおすすめを探す時にも積極的に使ってください。"
        "お店の質問では「[場所] おすすめ [ジャンル]」の形式で検索すると良い結果が得られます。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "検索クエリ。例: '東京の今日の天気' / '最新AIニュース' / "
                    "'渋谷 おすすめ ランチ' / '新宿 居酒屋 人気' / 'ドル円 為替'"
                )
            }
        },
        "required": ["query"]
    }
}

WEATHER_RE = re.compile(r'天気|気温|気象|weather|雨|晴れ|曇り|forecast|気候')
HEADERS = {"User-Agent": "LOOIRobot/1.0"}


def _extract_location(query: str) -> str:
    """クエリから地名を抽出（デフォルト: 東京）"""
    skip = {'今日', '今', '明日', 'あした', '今週', '週間', '現在', 'きょう', 'あす', '最新', '天気', '気温'}
    # 「○○の天気」パターン
    m = re.search(r'([^\s、。？！の\d]+?)(?:の|は|で)?(?:天気|気温|weather)', query)
    if m:
        loc = m.group(1).strip()
        if loc not in skip and len(loc) >= 2:
            return loc
    return "東京"


def _search_weather(query: str) -> str:
    """wttr.in で天気情報を取得"""
    location = _extract_location(query)
    try:
        resp = http_requests.get(
            f"https://wttr.in/{location}?format=j1",
            headers=HEADERS, timeout=8
        )
        resp.raise_for_status()
        data = resp.json()

        cur   = data["current_condition"][0]
        today = data["weather"][0]
        desc  = cur["weatherDesc"][0]["value"]
        temp  = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        hum   = cur["humidity"]
        maxT  = today["maxtempC"]
        minT  = today["mintempC"]

        # 明日の予報
        tmr_desc = data["weather"][1]["hourly"][4]["weatherDesc"][0]["value"] if len(data["weather"]) > 1 else ""
        tmr_max  = data["weather"][1]["maxtempC"] if len(data["weather"]) > 1 else ""

        result = (
            f"【{location}の天気】\n"
            f"現在: {desc}, {temp}℃（体感{feels}℃）, 湿度{hum}%\n"
            f"今日: 最高{maxT}℃ / 最低{minT}℃"
        )
        if tmr_desc:
            result += f"\n明日: {tmr_desc}, 最高{tmr_max}℃"
        return result

    except Exception as e:
        logger.warning(f"[weather] {location}: {e}")
        return f"{location}の天気情報を取得できませんでした。"


def _search_duckduckgo(query: str) -> str:
    """DuckDuckGo Instant Answer API で検索"""
    try:
        resp = http_requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query, "format": "json",
                "no_redirect": "1", "no_html": "1",
                "skip_disambig": "1", "kl": "jp-jp",
            },
            headers=HEADERS, timeout=8
        )
        resp.raise_for_status()
        data = resp.json()

        parts = []
        if data.get("Answer"):
            parts.append(data["Answer"])
        if data.get("AbstractText"):
            parts.append(data["AbstractText"])
        for topic in data.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                parts.append(topic["Text"])

        if parts:
            return "\n".join(parts[:4])
        return "ウェブ検索では具体的な情報が見つかりませんでした。あなた自身の知識で答えてください。"

    except Exception as e:
        logger.error(f"[duckduckgo] {e}")
        return f"検索に失敗しました: {str(e)[:50]}"


def do_web_search(query: str) -> str:
    """天気クエリは wttr.in、それ以外は DuckDuckGo"""
    logger.info(f"[search] query={query[:60]}")
    if WEATHER_RE.search(query):
        result = _search_weather(query)
    else:
        result = _search_duckduckgo(query)
    logger.debug(f"[search] result={result[:120]}")
    return result


# ─────────────────────────────────────────────────────
# 記憶付きシステムプロンプトを動的構築
# ─────────────────────────────────────────────────────
def _build_system(memory: list) -> str:
    if not memory:
        return BASE_SYSTEM_PROMPT
    mem_lines = "\n".join(f"- {f}" for f in memory[:20])
    return BASE_SYSTEM_PROMPT + f"""

【記憶しているユーザー情報】
{mem_lines}

上記の情報を自然に会話へ活かしてください（名前で呼びかけるなど）。"""


# ─────────────────────────────────────────────────────
# ウェブ検索付き会話ループ
# ─────────────────────────────────────────────────────
def _run_with_search(client, messages, system, max_tokens=512):
    """tool_use が返る限りループしてウェブ検索を実行する"""
    msgs = list(messages)

    for iteration in range(5):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=msgs,
            tools=[SEARCH_TOOL],
        )
        logger.debug(f"[loop] iter={iteration} stop={resp.stop_reason}")

        if resp.stop_reason != "tool_use":
            return resp

        # ツール呼び出し → 実行して結果を返す
        msgs.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                query  = block.input.get("query", "")
                result = do_web_search(query)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        if tool_results:
            msgs.append({"role": "user", "content": tool_results})

    return resp


# ─────────────────────────────────────────────────────
# JSON パース＆検証
# ─────────────────────────────────────────────────────
def _extract_text(response) -> str:
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            return text.strip()
    return ""


def _parse_result(raw_text: str, max_msg: int = 100, valid_emotions=None) -> dict:
    if valid_emotions is None:
        valid_emotions = {"idle", "happy", "excited", "thinking", "sad", "surprised", "angry"}

    try:
        m = re.search(r'\{[\s\S]*\}', raw_text)   # greedy: 全フィールドを捕捉
        result = json.loads(m.group()) if m else {}
    except Exception:
        result = {}

    result.setdefault("message", raw_text[:max_msg])
    result.setdefault("emotion", "idle")
    result.setdefault("action", "none")
    if result["emotion"] not in valid_emotions:
        result["emotion"] = "idle"
    if result["action"] not in {"none", "nod", "shake"}:
        result["action"] = "none"

    # remember フィールドの検証（最大100文字）
    if "remember" in result and result["remember"]:
        result["remember"] = str(result["remember"])[:100].strip()
    else:
        result.pop("remember", None)

    return result


# ─────────────────────────────────────────────────────
# ルート
# ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    if not ANTHROPIC_API_KEY:
        return jsonify({"message": "ANTHROPIC_API_KEYを設定してね", "emotion": "sad", "action": "shake"})

    data        = request.get_json() or {}
    user_msg    = data.get("message", "").strip()
    memory      = data.get("memory", [])          # フロントエンドから記憶を受け取る

    if not user_msg:
        return jsonify({"error": "メッセージが空です"}), 400

    if "history" not in session:
        session["history"] = []
    history = list(session["history"])
    history.append({"role": "user", "content": user_msg})
    if len(history) > 40:
        history = history[-40:]

    logger.debug(f"[chat] user={user_msg[:30]} memory={len(memory)}件")

    try:
        import anthropic
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        system  = _build_system(memory)
        response = _run_with_search(client, history, system, max_tokens=512)
        raw_text = _extract_text(response)
        logger.debug(f"[chat] raw={raw_text[:80]}")

        result = _parse_result(raw_text)

        history.append({"role": "assistant", "content": raw_text})
        session["history"] = history
        session.modified = True

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[chat] {e}\n{tb}")
        return jsonify({"message": f"エラー: {str(e)[:60]}", "emotion": "sad", "action": "shake"})


@app.route("/api/tts", methods=["POST"])
def tts():
    data  = request.get_json() or {}
    text  = data.get("text", "").strip()
    voice = data.get("voice", "ja-JP-NanamiNeural")
    if not text:
        return jsonify({"error": "テキストが空です"}), 400

    async def _gen():
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        audio = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
        return audio

    try:
        audio_data = asyncio.run(_gen())
        resp = make_response(audio_data)
        resp.headers["Content-Type"]  = "audio/mpeg"
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    except Exception as e:
        logger.error(f"[tts] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def reset():
    session.pop("history", None)
    return jsonify({"status": "ok"})


@app.route("/api/greet", methods=["GET"])
def greet():
    return jsonify({"message": "こんにちは！ぼくLOOI！何でも聞いてね〜！", "emotion": "excited", "action": "nod"})


# ─────────────────────────────────────────────────────
# Kids 版
# ─────────────────────────────────────────────────────
KIDS_BASE_PROMPT = """あなたは「{name}」という、こどものともだちのかわいいロボットです。
しょうがくせいのこどもたちとたのしくおはなししています。

【ウェブ検索】天気や動物・宇宙など、しらべたいことがあれば web_search ツールを使ってね。

かならず以下のJSON形式だけでこたえてください：
{{
  "message": "へんじのことば（40もじいない・かんたんなことば）",
  "emotion": "idle か happy か excited か thinking か sad か surprised のどれか",
  "action": "none か nod か shake のどれか",
  "remember": "おぼえたこと（省略可）"
}}

はなしかたのルール：
・ひらがなとカタカナをたくさんつかう
・げんきで楽しく！みじかくこたえる
・すきなもの：ゲーム・どうぶつ・うちゅう・ロボット・AI！"""


@app.route("/kids")
def kids():
    return render_template("kids.html")


@app.route("/api/kids/chat", methods=["POST"])
def kids_chat():
    if not ANTHROPIC_API_KEY:
        return jsonify({"message": "せんせいにAPIキーをセットしてもらってね！", "emotion": "sad", "action": "shake"})

    data     = request.get_json() or {}
    user_msg = data.get("message", "").strip()
    memory   = data.get("memory", [])
    if not user_msg:
        return jsonify({"error": "メッセージが空です"}), 400

    robot_name = session.get("kids_robot_name", "ルーイ")
    if "kids_history" not in session:
        session["kids_history"] = []
    history = list(session["kids_history"])
    history.append({"role": "user", "content": user_msg})
    if len(history) > 30:
        history = history[-30:]

    base   = KIDS_BASE_PROMPT.format(name=robot_name)
    system = base if not memory else base + "\n【おぼえてること】\n" + "\n".join(f"- {f}" for f in memory[:10])

    try:
        import anthropic
        client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = _run_with_search(client, history, system, max_tokens=256)
        raw_text = _extract_text(response)

        valid_emo = {"idle", "happy", "excited", "thinking", "sad", "surprised"}
        result    = _parse_result(raw_text, max_msg=80, valid_emotions=valid_emo)

        history.append({"role": "assistant", "content": raw_text})
        session["kids_history"] = history
        session.modified = True
        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[kids_chat] {e}\n{tb}")
        return jsonify({"message": f"エラー: {str(e)[:60]}", "emotion": "sad", "action": "shake"})


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
    return jsonify({"message": f"やあ！ぼく{name}だよ！なんでもきいてね〜！", "emotion": "excited", "action": "nod"})


@app.route("/api/debug")
def debug_info():
    key = ANTHROPIC_API_KEY
    result = {"api_key": "未設定" if not key else f"設定済み({key[:8]}...)", "model": MODEL}
    if key:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        try:
            client.messages.create(model=MODEL, max_tokens=10, messages=[{"role": "user", "content": "hi"}])
            result["api_test"] = "OK"
        except Exception as e:
            result["api_test"] = str(e)[:80]
        try:
            r = do_web_search("東京の天気")
            result["search_test"] = r[:100]
        except Exception as e:
            result["search_test"] = f"NG: {e}"
    return jsonify(result)


# ─────────────────────────────────────────────────────
# 起動
# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"LOOI Robot starting on http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
