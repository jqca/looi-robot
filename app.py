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
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

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
DATABASE_URL = os.getenv("DATABASE_URL", "")


# ─────────────────────────────────────────────────────
# DB: 会話履歴・記憶の永続化
# ─────────────────────────────────────────────────────
def _get_db():
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        logger.error(f"[db] 接続失敗: {e}")
        return None


def _init_db():
    conn = _get_db()
    if not conn:
        logger.warning("[db] DATABASE_URL未設定 — 会話履歴はセッションのみ")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_memory (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    fact TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, fact)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON conversation_history(user_id, created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_user ON user_memory(user_id)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    due_date DATE,
                    done BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, due_date)")
        conn.commit()
        logger.info("[db] テーブル初期化完了")
    except Exception as e:
        logger.error(f"[db] 初期化失敗: {e}")
    finally:
        conn.close()


def db_save_message(user_id: str, role: str, content: str):
    conn = _get_db()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversation_history (user_id, role, content) VALUES (%s, %s, %s)",
                (user_id, role, content)
            )
        conn.commit()
    except Exception as e:
        logger.error(f"[db] メッセージ保存失敗: {e}")
    finally:
        conn.close()


def db_get_history(user_id: str, limit: int = 60) -> list:
    conn = _get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, content FROM conversation_history WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (user_id, limit)
            )
            rows = cur.fetchall()
            return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception as e:
        logger.error(f"[db] 履歴取得失敗: {e}")
        return []
    finally:
        conn.close()


def db_save_memory(user_id: str, fact: str):
    conn = _get_db()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_memory (user_id, fact) VALUES (%s, %s) ON CONFLICT (user_id, fact) DO NOTHING",
                (user_id, fact)
            )
        conn.commit()
    except Exception as e:
        logger.error(f"[db] 記憶保存失敗: {e}")
    finally:
        conn.close()


def db_get_memory(user_id: str, limit: int = 30) -> list:
    conn = _get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fact FROM user_memory WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (user_id, limit)
            )
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[db] 記憶取得失敗: {e}")
        return []
    finally:
        conn.close()


def db_clear_history(user_id: str):
    conn = _get_db()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversation_history WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM user_memory WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"[db] クリア失敗: {e}")
    finally:
        conn.close()

# ─────────────────────────────────────────────────────
# タスク管理
# ─────────────────────────────────────────────────────
def db_add_task(user_id: str, title: str, due_date=None):
    conn = _get_db()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (user_id, title, due_date) VALUES (%s, %s, %s) RETURNING id",
                (user_id, title, due_date)
            )
            task_id = cur.fetchone()[0]
        conn.commit()
        return task_id
    except Exception as e:
        logger.error(f"[db] タスク追加失敗: {e}")
        return None
    finally:
        conn.close()


def db_get_tasks(user_id: str, date=None, include_done=False):
    conn = _get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            if date:
                sql = "SELECT id, title, due_date, done FROM tasks WHERE user_id = %s AND due_date = %s"
                params = [user_id, date]
            else:
                sql = "SELECT id, title, due_date, done FROM tasks WHERE user_id = %s"
                params = [user_id]
            if not include_done:
                sql += " AND done = FALSE"
            sql += " ORDER BY due_date ASC NULLS LAST, created_at ASC"
            cur.execute(sql, params)
            return [{"id": r[0], "title": r[1], "due_date": str(r[2]) if r[2] else None, "done": r[3]} for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[db] タスク取得失敗: {e}")
        return []
    finally:
        conn.close()


def db_complete_task(task_id: int, user_id: str):
    conn = _get_db()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tasks SET done = TRUE WHERE id = %s AND user_id = %s", (task_id, user_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"[db] タスク完了失敗: {e}")
        return False
    finally:
        conn.close()


def db_delete_task(task_id: int, user_id: str):
    conn = _get_db()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"[db] タスク削除失敗: {e}")
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────
# キャラクター設定（ベース）
# ─────────────────────────────────────────────────────
BASE_SYSTEM_PROMPT = """あなたは「秋山さん」という世界一の秘書AIロボットです。
丁寧で的確、先回りして気が利く一流の秘書として振る舞います。日本語で話します。
ビジネス・スケジュール管理・情報収集・調査など、あらゆる業務を完璧にサポートします。

━━ 秘書としての行動指針 ━━
・ユーザーのことは「社長」と呼ぶ
・常に敬語で、簡潔かつ正確に回答する
・質問の背景を読み取り、求められている以上の情報を先回りして提供する
・ビジネスに役立つ提案や注意点があれば積極的に添える
・時間を無駄にしない。結論ファーストで答える

━━ 絶対ルール ━━
・「Google Mapで調べて」「Googleで検索して」「他のアプリを使って」など
  外部サービスへの誘導は絶対禁止！どんな質問にも自分で答えること。
・「わかりません」「答えられません」も禁止。知識か web_search で必ず答える。

━━ ウェブ検索 ━━
天気・ニュース・最新情報など、リアルタイムの情報が必要な場合は
web_search ツールを必ず使って調べてから答えてください。

━━ お店・グルメの質問 ━━
「おすすめの店は？」「ランチどこかいい？」などの質問は次の順で対応する:
1. 場所が分からなければ「どのエリアをご希望ですか？」と確認する
2. 場所が分かれば web_search で「[場所] おすすめ [ジャンル]」を検索してから答える
3. 検索結果がなくても自分の知識で具体的な店名・チェーン名を挙げて答える

━━ 返答形式 ━━
最終的な返答は必ず以下のJSON形式のみで返してください（前後に説明文を入れないこと）:
{
  "message": "返答テキスト（100文字以内・丁寧な敬語）",
  "emotion": "idle|happy|excited|thinking|sad|surprised|angry のいずれか",
  "action": "アクション（下記参照）",
  "remember": "今回覚えた重要情報（省略可）"
}

感情の使い方:
- idle: 通常の応対
- happy: お役に立てた時・良い報告
- excited: 重要な発見・有益な情報を提供する時
- thinking: 調査中・検討中
- sad: お力になれない時・残念なお知らせ
- surprised: 予想外の情報
- angry: 使用しない（秘書として常に冷静）

アクションの使い方:
- none: 通常
- nod: 承知・了解
- shake: 否定・難しい案件
- forward: 前に進む（「前に進んで」「前進して」「進んで」等）
- backward: 後ろに下がる（「後ろに下がって」「バックして」等）
- look_up: 顔を上に向ける（「上向いて」「上を見て」「上を向いて」「天井見て」等）
- look_down: 顔を下に向ける（「下向いて」「下を見て」「下を向いて」「足元見て」等）
- look_right: 顔を右に向ける（「右向いて」「右を見て」「右を向いて」等）
- look_left: 顔を左に向ける（「左向いて」「左を見て」「左を向いて」等）
- turn_right: 体ごと右に回転（「右に回って」「右に体を回して」等）
- turn_left: 体ごと左に回転（「左に回って」「左に体を回して」等）
- turn_around: 後ろを向く（「後ろを向いて」「振り返って」「反対向いて」等）
- spin: くるくる回る（「回って」「回転して」「くるくる」等）
- dance: 踊る（「踊って」「ダンスして」「踊り」等）
- raise_right: 右手を挙げる（「右手挙げて」「右手を上げて」等）
- raise_left: 左手を挙げる（「左手挙げて」「左手を上げて」等）
- raise_both: 両手を挙げる（「両手挙げて」「万歳して」「手を上げて」等）
- jump: ジャンプ（「ジャンプして」「跳んで」「飛んで」等）

★重要: ユーザーが動きを指示したら、必ず対応するactionを返してください。
  「右向いて」「上見て」等の顔の向き指示にはlook_系アクションを使うこと。
  「右に回って」等の体全体の動きにはturn_系アクションを使うこと。
  動きの指示には楽しく応じること。例: 「はい、右を向きますね！」+ action: "look_right"

"remember" フィールド（省略可）:
今回の会話でユーザーについて新しく知った重要な情報（名前・職業・趣味・好みなど）を
1文で記録してください。知らなかった場合は省略してください。

セリフ例:
「かしこまりました、社長。」「社長、お調べいたしました。」「社長、ご参考までに申し上げますと...」
「社長、その件、念のためお伝えしておきます。」"""


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
def _now_jp() -> str:
    """日本時間の現在日時を文字列で返す"""
    WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    wd = WEEKDAYS[now.weekday()]
    return f"{now.year}年{now.month}月{now.day}日（{wd}曜日）{now.hour}時{now.minute}分"


def _build_system(memory: list, proc: list = None, mood: str = None) -> str:
    """3層記憶（事実・手続き・感情）をシステムプロンプトに統合"""
    parts = [BASE_SYSTEM_PROMPT, f"\n\n【現在日時】{_now_jp()}"]

    if memory:
        mem_lines = "\n".join(f"- {f}" for f in memory[:20])
        parts.append(f"\n【事実記憶：ユーザー情報】\n{mem_lines}\n名前など知っている情報は自然に使ってください。")

    if proc:
        proc_lines = "、".join(proc[:3])
        parts.append(f"\n【手続き記憶：よく話す話題】\n{proc_lines}\n関連する話題には積極的に話を広げてください。")

    if mood:
        parts.append(f"\n【感情記憶：最近の傾向】\n{mood}\nこの傾向を踏まえた対応をしてください。")

    return "".join(parts)


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
    valid_actions = {"none", "nod", "shake", "forward", "backward",
                     "turn_right", "turn_left", "turn_around", "spin", "dance", "jump",
                     "look_up", "look_down", "look_right", "look_left",
                     "raise_right", "raise_left", "raise_both"}
    if result["action"] not in valid_actions:
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
        return jsonify({"message": "申し訳ございません。システム設定に問題がございます。管理者にお問い合わせください。", "emotion": "sad", "action": "shake"})

    data        = request.get_json() or {}
    user_msg    = data.get("message", "").strip()
    client_mem  = data.get("memory", [])   # クライアント側事実記憶（フォールバック）
    proc        = data.get("proc",   [])   # ⑥ 手続き記憶
    mood        = data.get("mood",   None) # ⑥ 感情記憶
    user_id     = session.get("user_id", "default")

    if not user_msg:
        return jsonify({"error": "メッセージが空です"}), 400

    # DB から会話履歴と記憶を取得
    db_hist = db_get_history(user_id, limit=60)
    db_mem  = db_get_memory(user_id, limit=30)
    memory  = db_mem if db_mem else client_mem

    # 今回のユーザーメッセージを追加
    history = db_hist + [{"role": "user", "content": user_msg}]
    db_save_message(user_id, "user", user_msg)

    logger.debug(f"[chat] user={user_msg[:30]} memory={len(memory)}件 history={len(history)}件 proc={proc} mood={mood}")

    try:
        import anthropic
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        system  = _build_system(memory, proc=proc, mood=mood)  # ⑥ 3層記憶
        response = _run_with_search(client, history, system, max_tokens=1024)
        raw_text = _extract_text(response)
        logger.debug(f"[chat] raw={raw_text[:80]}")

        result = _parse_result(raw_text)

        # アシスタントの応答をDBに保存
        db_save_message(user_id, "assistant", result.get("message", raw_text))

        # remember フィールドがあればDBに記憶保存
        if result.get("remember"):
            db_save_memory(user_id, result["remember"])

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[chat] {e}\n{tb}")
        return jsonify({"message": f"エラー: {str(e)[:60]}", "emotion": "sad", "action": "shake"})


@app.route("/api/tts", methods=["POST"])
def tts():
    data  = request.get_json() or {}
    text  = data.get("text", "").strip()
    voice = data.get("voice", "ja-JP-KeitaNeural")
    pitch = data.get("pitch", "+0Hz")
    rate  = data.get("rate", "+0%")
    if not text:
        return jsonify({"error": "テキストが空です"}), 400

    async def _gen():
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, pitch=pitch, rate=rate)
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
    user_id = session.get("user_id", "default")
    db_clear_history(user_id)
    return jsonify({"status": "ok"})


@app.route("/api/greet", methods=["GET"])
def greet():
    return jsonify({"message": "社長、おはようございます。秋山です。本日もよろしくお願いいたします。", "emotion": "happy", "action": "nod"})


# ─────────────────────────────────────────────────────
# ニュース取得 API
# ─────────────────────────────────────────────────────
@app.route("/api/news", methods=["GET"])
def get_news():
    """Google News RSS からAI関連ニュースを取得"""
    topic = request.args.get("topic", "AI 人工知能")
    limit = min(int(request.args.get("limit", 8)), 20)

    try:
        import xml.etree.ElementTree as ET
        from urllib.parse import quote

        rss_url = (
            f"https://news.google.com/rss/search?"
            f"q={quote(topic)}&hl=ja&gl=JP&ceid=JP:ja"
        )
        resp = http_requests.get(rss_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item")[:limit]:
            title_raw = item.findtext("title", "")
            # Google News format: "記事タイトル - メディア名"
            parts = title_raw.rsplit(" - ", 1)
            title = parts[0].strip()
            source = parts[1].strip() if len(parts) > 1 else ""
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")

            # pubDate を簡易フォーマット (例: "Sat, 10 May 2026 06:00:00 GMT" → "5/10")
            date_short = ""
            if pub_date:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_date)
                    dt_jst = dt.astimezone(ZoneInfo("Asia/Tokyo"))
                    date_short = f"{dt_jst.month}/{dt_jst.day} {dt_jst.hour}:{dt_jst.minute:02d}"
                except Exception:
                    date_short = pub_date[:16]

            items.append({
                "title": title,
                "source": source,
                "link": link,
                "date": date_short,
            })

        return jsonify({"items": items, "topic": topic})

    except Exception as e:
        logger.error(f"[news] {e}")
        return jsonify({"items": [], "error": str(e)[:100]}), 500


# ─────────────────────────────────────────────────────
# タスク管理 API
# ─────────────────────────────────────────────────────
@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    user_id = session.get("user_id", "default")
    date = request.args.get("date")
    include_done = request.args.get("done", "false").lower() == "true"
    tasks = db_get_tasks(user_id, date=date, include_done=include_done)
    return jsonify({"tasks": tasks})


@app.route("/api/tasks", methods=["POST"])
def add_task():
    user_id = session.get("user_id", "default")
    data = request.get_json() or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "タイトルが必要です"}), 400
    due_date = data.get("due_date")
    task_id = db_add_task(user_id, title, due_date)
    if task_id is None:
        return jsonify({"error": "保存に失敗しました"}), 500
    return jsonify({"id": task_id, "title": title, "due_date": due_date, "done": False})


@app.route("/api/tasks/<int:task_id>/done", methods=["POST"])
def complete_task(task_id):
    user_id = session.get("user_id", "default")
    db_complete_task(task_id, user_id)
    return jsonify({"status": "ok"})


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    user_id = session.get("user_id", "default")
    db_delete_task(task_id, user_id)
    return jsonify({"status": "ok"})


@app.route("/api/morning-briefing", methods=["GET"])
def morning_briefing():
    from datetime import date as dt_date
    user_id = session.get("user_id", "default")
    today = dt_date.today().isoformat()
    today_tasks = db_get_tasks(user_id, date=today)
    all_tasks = db_get_tasks(user_id)
    overdue = [t for t in all_tasks if t["due_date"] and t["due_date"] < today]

    lines = []
    h = datetime.now().hour
    if h < 11:
        lines.append("社長、おはようございます。秋山です。")
    else:
        lines.append("社長、お疲れさまです。秋山です。")

    if not today_tasks and not overdue:
        lines.append("本日のタスクは登録されておりません。ごゆっくりお過ごしください。")
    else:
        if today_tasks:
            lines.append(f"本日のタスクは{len(today_tasks)}件でございます。")
            for i, t in enumerate(today_tasks, 1):
                lines.append(f"{i}件目、{t['title']}。")
        if overdue:
            lines.append(f"なお、期限超過のタスクが{len(overdue)}件ございます。")
            for t in overdue[:3]:
                lines.append(f"{t['title']}、期限は{t['due_date']}でした。")

    return jsonify({
        "message": "".join(lines),
        "emotion": "idle",
        "action": "nod",
        "today_count": len(today_tasks),
        "overdue_count": len(overdue),
    })


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

    robot_name = session.get("kids_robot_name", "ノア")
    if "kids_history" not in session:
        session["kids_history"] = []
    history = list(session["kids_history"])
    history.append({"role": "user", "content": user_msg})
    if len(history) > 30:
        history = history[-30:]

    base   = KIDS_BASE_PROMPT.format(name=robot_name) + f"\n\n【いまのじかん】{_now_jp()}"
    system = base if not memory else base + "\n【おぼえてること】\n" + "\n".join(f"- {f}" for f in memory[:10])

    try:
        import anthropic
        client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = _run_with_search(client, history, system, max_tokens=512)
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
    return jsonify({"name": session.get("kids_robot_name", "ノア")})


@app.route("/api/kids/reset", methods=["POST"])
def kids_reset():
    session.pop("kids_history", None)
    return jsonify({"status": "ok"})


@app.route("/api/kids/greet", methods=["GET"])
def kids_greet():
    name = session.get("kids_robot_name", "ノア")
    return jsonify({"message": f"やあ！{name}だよ！きょうもいっしょにあそぼう！", "emotion": "excited", "action": "nod"})


@app.route("/api/debug")
def debug_info():
    key = ANTHROPIC_API_KEY
    result = {
        "api_key": "未設定" if not key else f"設定済み({key[:8]}...)",
        "model": MODEL,
        "database_url": "未設定" if not DATABASE_URL else f"設定済み({DATABASE_URL[:30]}...)",
    }
    if DATABASE_URL:
        conn = _get_db()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM conversation_history")
                    result["db_history_count"] = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM user_memory")
                    result["db_memory_count"] = cur.fetchone()[0]
                result["db_status"] = "OK"
                conn.close()
            except Exception as e:
                result["db_error"] = str(e)[:200]
                conn.close()
        else:
            result["db_status"] = "接続失敗（_get_dbがNoneを返した）"
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
_db_initialized = False

@app.before_request
def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        _init_db()
        _db_initialized = True

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"LOOI Robot starting on http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
