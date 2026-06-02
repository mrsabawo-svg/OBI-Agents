"""
OBI Agents — Intelligence Agent
The brain that reads all agent outputs and produces
a final verdict using Groq (LLaMA 3.3 70B) + Claude.
"""
import os, json, requests
from core.utils import sast_str
from core.memory import load as load_memory, save as save_memory

GROQ_API_KEY      = os.environ.get("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID")
GIST_ID           = os.environ.get("GIST_ID")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN")

class IntelligenceAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def verdict(self, payload: dict) -> dict:
        memory   = load_memory()
        accuracy = memory.get(self.symbol, {}).get("accuracy", "No history yet")

        # ── Groq: systematic reasoning ─────────────────────
        groq_verdict = self._ask_groq(payload, accuracy)

        # ── Claude: qualitative second opinion ─────────────
        claude_verdict = self._ask_claude(payload, groq_verdict)

        result = {
            "symbol":         self.symbol,
            "timestamp":      sast_str(),
            "direction":      payload["trigger"].get("direction"),
            "grade":          payload["trigger"].get("grade"),
            "entry":          payload["trigger"].get("entry"),
            "sl":             payload["trigger"].get("sl"),
            "tp1":            payload["trigger"].get("tp1"),
            "tp2":            payload["trigger"].get("tp2"),
            "tp3":            payload["trigger"].get("tp3"),
            "rr":             payload["trigger"].get("rr"),
            "tags":           payload["trigger"].get("tags", []),
            "groq_verdict":   groq_verdict,
            "claude_verdict": claude_verdict,
        }

        self._update_memory(memory, result)
        self._push_to_gist(result)
        self._send_telegram(result)
        return result

    # ── Groq ───────────────────────────────────────────────

    def _ask_groq(self, payload: dict, accuracy: str) -> str:
        try:
            prompt = f"""You are a professional forex and crypto trading analyst.
Review this multi-agent trading signal and give a systematic verdict.

SIGNAL PAYLOAD:
{json.dumps(payload, indent=2)}

HISTORICAL ACCURACY FOR {self.symbol}: {accuracy}

Respond with:
1. VERDICT: TAKE IT / LEAVE IT / WAIT
2. CONFIDENCE: 1-10
3. STRENGTHS: (bullet points)
4. CONCERNS: (bullet points)
5. WATCH: one thing to monitor after entry

Be concise. Maximum 200 words."""

            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 300}
            )
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"Groq error: {e}"

    # ── Claude ─────────────────────────────────────────────

    def _ask_claude(self, payload: dict, groq_verdict: str) -> str:
        try:
            prompt = f"""You are a senior trading analyst reviewing an AI-generated signal.
The systematic agents have already analysed this. Groq's verdict was:

{groq_verdict}

Full signal:
{json.dumps(payload, indent=2)}

Add qualitative insight the agents may have missed.
Give your verdict in this exact format:
VERDICT: TAKE IT / LEAVE IT / WAIT
CONFIDENCE: X/10
LIKES: (2-3 bullets)
CONCERNS: (1-2 bullets)
WATCH: one post-entry thing
Maximum 150 words."""

            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "Content-Type": "application/json"},
                json={"model": "claude-sonnet-4-20250514",
                      "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]}
            )
            return r.json()["content"][0]["text"].strip()
        except Exception as e:
            return f"Claude error: {e}"

    # ── Memory ─────────────────────────────────────────────

    def _update_memory(self, memory: dict, result: dict):
        if self.symbol not in memory:
            memory[self.symbol] = {"signals": 0, "accuracy": "No history yet"}
        memory[self.symbol]["signals"] = memory[self.symbol].get("signals", 0) + 1
        memory[self.symbol]["last_signal"] = result.get("timestamp")
        memory[self.symbol]["last_direction"] = result.get("direction")
        save_memory(memory)

    # ── Gist ───────────────────────────────────────────────

    def _push_to_gist(self, result: dict):
        try:
            requests.patch(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GITHUB_TOKEN}"},
                json={"files": {"obi_signal.json": {"content": json.dumps(result, indent=2)}}}
            )
        except Exception as e:
            print(f"[INTEL] Gist push failed: {e}")

    # ── Telegram ───────────────────────────────────────────

    def _send_telegram(self, r: dict):
        try:
            gv = r.get("groq_verdict", "")
            cv = r.get("claude_verdict", "")
            tags = " + ".join(r.get("tags", [])) or "—"

            msg = (
                f"🤖 *OBI SIGNAL — {r['symbol']}*\n"
                f"Grade: `{r['grade']}`  |  {r['direction']}\n"
                f"Entry: `{r['entry']}`\n"
                f"SL: `{r['sl']}`\n"
                f"TP1: `{r['tp1']}`  TP2: `{r['tp2']}`  TP3: `{r['tp3']}`\n"
                f"RR: `{r['rr']}`  |  Tags: {tags}\n"
                f"{'─'*30}\n"
                f"🦙 *GROQ VERDICT*\n{gv}\n"
                f"{'─'*30}\n"
                f"🧠 *CLAUDE VERDICT*\n{cv}\n"
                f"{'─'*30}\n"
                f"🕐 {r['timestamp']}"
            )
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID,
                      "text": msg,
                      "parse_mode": "Markdown"}
            )
        except Exception as e:
            print(f"[INTEL] Telegram failed: {e}")
