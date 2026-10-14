"use client";

import { useEffect, useRef, useState } from "react";
import { Mic, MicOff, Send, Sparkles, Volume2, VolumeX } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";

type Msg = { role: "user" | "assistant"; text: string; pending?: boolean };
const ASK = ["When is my train?", "What's on day 2?", "Kitna paisa bacha hai?"];

/** The trip chat (ADR 0016): ask anything about this trip, or say a change, by typing or by voice.
 *  Questions are answered from this plan only; a change is sent through the normal re-plan (`onChange`), and its result
 *  comes back as the reply. Spoken questions get spoken answers. The conversation is kept per trip in this browser. */
export function TripChat({ tripId, chips, busy, readOnly, replansLeft, onChange }: {
  tripId: string; chips: string[]; busy: boolean; readOnly: boolean; replansLeft: number;
  onChange: (text: string) => Promise<string>;
}) {
  const key = `yaitro.chat.${tripId}`;
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [thinking, setThinking] = useState(false);
  const [listening, setListening] = useState(false);
  const [speak, setSpeak] = useState(true);
  const [lang, setLang] = useState<"en-IN" | "hi-IN">("en-IN");
  const recRef = useRef<any>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const voiceTurn = useRef(false);
  const [canListen, setCanListen] = useState(false);  // known only in the browser: set after the first render
  useEffect(() => { setCanListen(!!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)); }, []);

  useEffect(() => { try { const s = sessionStorage.getItem(key); if (s) setMsgs(JSON.parse(s)); } catch { /* storage unavailable */ } }, [key]);
  useEffect(() => {
    try { sessionStorage.setItem(key, JSON.stringify(msgs.filter((m) => !m.pending).slice(-30))); } catch { /* storage unavailable */ }
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, key]);
  useEffect(() => () => { recRef.current?.abort?.(); window.speechSynthesis?.cancel(); }, []);

  function say(reply: string) {
    if (!speak || !voiceTurn.current || typeof window === "undefined" || !window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(reply);
    u.lang = lang;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  async function send(raw: string, byVoice = false) {
    const message = raw.trim();
    if (!message || thinking || busy) return;
    voiceTurn.current = byVoice;
    if (readOnly) { await onChange(message); return; }  // the sample trip: sends them to plan their own
    const history = msgs.filter((m) => !m.pending).slice(-6).map(({ role, text }) => ({ role, text }));
    setMsgs((m) => [...m, { role: "user", text: message }]);
    setText(""); setThinking(true);
    try {
      const out = await endpoints.chat(tripId, message, history);
      if (out.action === "change") {
        setMsgs((m) => [...m, { role: "assistant", text: "On it: updating your plan…", pending: true }]);
        const result = await onChange(out.message || message);  // without any text pasted from the plan
        setMsgs((m) => [...m.filter((x) => !x.pending), { role: "assistant", text: result }]);
        say(result);
      } else {
        const reply = out.text || "Sorry, I didn't get that.";
        setMsgs((m) => [...m, { role: "assistant", text: reply }]);
        say(reply);
      }
    } catch (e) {
      const err = e instanceof ApiError ? e.message : "I couldn't reach the planner. Try again in a moment.";
      setMsgs((m) => [...m.filter((x) => !x.pending), { role: "assistant", text: err }]);
    } finally { setThinking(false); }
  }

  function listen() {
    if (listening) { recRef.current?.stop(); return; }
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;
    const rec = new SR();
    rec.lang = lang; rec.interimResults = true; rec.maxAlternatives = 1;
    let finalText = "";
    rec.onresult = (e: any) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
      }
      setText((finalText + interim).trim());
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => { setListening(false); if (finalText.trim()) send(finalText, true); };
    recRef.current = rec;
    window.speechSynthesis?.cancel();
    setListening(true);
    rec.start();
  }

  const working = thinking || busy;
  return (
    <section className="card flex flex-col p-4" aria-label="Trip assistant">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-[16px] font-bold"><Sparkles className="h-4 w-4 text-signal-deep" aria-hidden />Trip assistant</h2>
          <p className="mt-0.5 text-[13px] text-ink-mute">
            {readOnly ? "This is a sample trip. Plan your own to chat about it." : "Ask anything about this trip, or tell it a change. Type or speak."}
          </p>
        </div>
        <button type="button" onClick={() => { setSpeak(!speak); window.speechSynthesis?.cancel(); }} aria-pressed={speak}
                title={speak ? "Spoken questions get spoken answers" : "Answers are text only"}
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-line text-ink-soft hover:border-ink/30">
          {speak ? <Volume2 className="h-4 w-4" aria-hidden /> : <VolumeX className="h-4 w-4" aria-hidden />}
          <span className="sr-only">{speak ? "Mute spoken answers" : "Speak answers"}</span>
        </button>
      </div>

      <div ref={listRef} className="mt-3 max-h-80 min-h-[3rem] space-y-2 overflow-y-auto pr-1" aria-live="polite">
        {msgs.length === 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {[...ASK, ...chips].map((c) => (
              <button key={c} type="button" className="chip-off !rounded-full !px-2.5 !py-1 text-[13px]" disabled={working} onClick={() => send(c)}>{c}</button>
            ))}
          </div>
        ) : msgs.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <p className={`max-w-[88%] whitespace-pre-line rounded-2xl px-3.5 py-2 text-[14px] leading-relaxed ${
              m.role === "user" ? "rounded-br-md bg-signal text-on-signal" : `rounded-bl-md bg-haze-2 text-ink ${m.pending ? "animate-pulse" : ""}`}`}>
              {m.text}
            </p>
          </div>
        ))}
        {thinking && !msgs.some((m) => m.pending) && (
          <div className="flex justify-start"><p className="rounded-2xl rounded-bl-md bg-haze-2 px-3.5 py-2 text-[14px] text-ink-mute">Thinking…</p></div>
        )}
      </div>

      {msgs.length > 0 && !working && (
        <div className="mt-2 flex gap-1.5 overflow-x-auto pb-1">
          {chips.slice(0, 4).map((c) => (
            <button key={c} type="button" className="chip-off shrink-0 whitespace-nowrap !rounded-full !px-2.5 !py-1 text-[12.5px]" onClick={() => send(c)}>{c}</button>
          ))}
        </div>
      )}

      <form className="mt-2 flex items-end gap-1.5" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <label htmlFor={`chat-${tripId}`} className="sr-only">Message</label>
        <textarea id={`chat-${tripId}`} rows={1} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} disabled={working}
                  placeholder={listening ? "Listening…" : "Ask or change…"}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(text); } }}
                  className="field min-h-[44px] flex-1 resize-none !py-2.5 text-[14px]" />
        {canListen && (
          <button type="button" onClick={listen} disabled={working && !listening} aria-pressed={listening}
                  className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl border transition-colors ${
                    listening ? "border-danger bg-danger-soft text-danger" : "border-line bg-paper text-ink hover:border-signal/50"}`}>
            {listening ? <MicOff className="h-4 w-4" aria-hidden /> : <Mic className="h-4 w-4" aria-hidden />}
            <span className="sr-only">{listening ? "Stop listening" : "Speak"}</span>
          </button>
        )}
        <button type="submit" disabled={working || !text.trim()} className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-signal text-on-signal disabled:opacity-50">
          <Send className="h-4 w-4" aria-hidden /><span className="sr-only">Send</span>
        </button>
      </form>
      <div className="mt-2 flex items-center justify-between text-[12px] text-ink-mute">
        {!readOnly ? <span className="num">{replansLeft} changes left</span> : <span />}
        {canListen && (
          <span className="inline-flex items-center gap-1" role="group" aria-label="Speaking language">
            Voice:
            {(["en-IN", "hi-IN"] as const).map((l) => (
              <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l}
                      className={`rounded-full px-2 py-0.5 font-semibold ${lang === l ? "bg-haze-2 text-ink" : "hover:text-ink"}`}>{l === "en-IN" ? "English" : "हिंदी"}</button>
            ))}
          </span>
        )}
      </div>
    </section>
  );
}
