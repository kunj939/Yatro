"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BookOpen, ExternalLink, MapPin, Maximize2, MessageCircle, Mic, MicOff, Minimize2, Send, Sparkles, Volume2, VolumeX, X } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";

type Source = { label: string; title?: string; url: string };
type Msg = { role: "user" | "assistant"; text: string; pending?: boolean; source?: Source | null; about?: string };
type Focus = { id: string; title: string; sight?: boolean };
const ASK = ["When is my train?", "What's on day 2?", "Kitna paisa bacha hai?"];
const FOLLOW_UP = ["Why is it famous?", "How long should I spend?", "Is it open then?", "Entry fee?"];
const FOLLOW_UP_OTHER = ["Is it open then?", "How much is it?", "How long is it?"];  // a meal or a hotel

/** Ask about one stop from anywhere on the page ("Ask" on a timeline card): the trip chat on screen picks it up. */
export function askAboutStop(item: { id: string; title: string; kind: string }) {
  const sight = item.kind === "activity" || item.kind === "sunset";
  const question = sight ? `Why is ${item.title} famous?` : `Tell me about ${item.title}`;
  window.dispatchEvent(new CustomEvent("yaitro:ask", { detail: { id: item.id, title: item.title, question, sight } }));
}

/** The trip chat (ADR 0016, 0017): ask anything about this trip or one of its stops, or say a change, by typing or by
 *  voice. Questions are answered from this plan only (and Wikipedia, for what a place is); a change is sent through
 *  the normal re-plan (`onChange`), and its result comes back as the reply. Spoken questions get spoken answers. The
 *  conversation is kept per trip in this browser. */
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
  const [focus, setFocus] = useState<Focus | null>(null);
  const [big, setBig] = useState(false);  // open large: a panel on a desktop, the whole screen on a phone
  const recRef = useRef<any>(null);
  const boxRef = useRef<HTMLElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const voiceTurn = useRef(false);
  const sendRef = useRef<(raw: string, byVoice?: boolean, about?: Focus | null) => void>(() => {});
  const [canListen, setCanListen] = useState(false);  // known only in the browser: set after the first render
  useEffect(() => { setCanListen(!!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)); }, []);

  useEffect(() => { try { const s = sessionStorage.getItem(key); if (s) setMsgs(JSON.parse(s)); } catch { /* storage unavailable */ } }, [key]);
  useEffect(() => {
    try { sessionStorage.setItem(key, JSON.stringify(msgs.filter((m) => !m.pending).slice(-30))); } catch { /* storage unavailable */ }
    // the newest question at the top, so a long answer is read from its first line
    const list = listRef.current, asked = list?.querySelectorAll<HTMLElement>("[data-asked]");
    const last = asked?.[asked.length - 1];
    list?.scrollTo({ top: last ? Math.max(0, last.offsetTop - 12) : list.scrollHeight, behavior: "smooth" });
  }, [msgs, key]);
  useEffect(() => () => { recRef.current?.abort?.(); window.speechSynthesis?.cancel(); }, []);
  useEffect(() => {
    if (!big) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setBig(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [big]);

  // "Ask" on a stop: only the chat that is on screen (phone or desktop layout) answers it, opened large beside the plan
  useEffect(() => {
    function onAsk(e: Event) {
      const d = (e as CustomEvent<{ id: string; title: string; question: string; sight: boolean }>).detail;
      if (!boxRef.current || boxRef.current.getClientRects().length === 0) return;
      setBig(true);
      const f = { id: d.id, title: d.title, sight: d.sight };
      setFocus(f);
      sendRef.current(d.question, false, f);
    }
    window.addEventListener("yaitro:ask", onAsk);
    return () => window.removeEventListener("yaitro:ask", onAsk);
  }, []);

  function say(reply: string) {
    if (!speak || !voiceTurn.current || typeof window === "undefined" || !window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(reply.replace(/\(Wikipedia\)/g, ""));
    u.lang = lang;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  async function send(raw: string, byVoice = false, about: Focus | null = focus) {
    const message = raw.trim();
    if (!message || thinking || busy) return;
    voiceTurn.current = byVoice;
    if (readOnly) { await onChange(message); return; }  // the sample trip: sends them to plan their own
    const history = msgs.filter((m) => !m.pending).slice(-6).map(({ role, text }) => ({ role, text }));
    setMsgs((m) => [...m, { role: "user", text: message, about: about?.title }]);
    setText(""); setThinking(true);
    try {
      const out = await endpoints.chat(tripId, message, history, about?.id);
      if (out.action === "change") {
        setMsgs((m) => [...m, { role: "assistant", text: "On it: updating your plan…", pending: true }]);
        const result = await onChange(out.message || message);  // without any text pasted from the plan
        setMsgs((m) => [...m.filter((x) => !x.pending), { role: "assistant", text: result }]);
        setFocus(null);  // the plan changed: the stop may have moved or gone
        say(result);
      } else {
        const reply = out.text || "Sorry, I didn't get that.";
        setMsgs((m) => [...m, { role: "assistant", text: reply, source: out.source ?? null }]);
        if (out.item_id && out.item_title) setFocus((f) => ({ id: out.item_id!, title: out.item_title!, sight: f && f.id === out.item_id ? f.sight : !!out.source }));
        say(reply);
      }
    } catch (e) {
      const err = e instanceof ApiError ? e.message : "I couldn't reach the planner. Try again in a moment.";
      setMsgs((m) => [...m.filter((x) => !x.pending), { role: "assistant", text: err }]);
    } finally { setThinking(false); }
  }
  sendRef.current = send;

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
  const suggestions = focus ? (focus.sight ? FOLLOW_UP : FOLLOW_UP_OTHER) : chips.slice(0, 4);
  const avatar = (
    <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-signal-soft text-signal-deep">
      <Sparkles className="h-3.5 w-3.5" aria-hidden />
    </span>
  );
  const panel = (
    <section ref={boxRef} aria-label="Trip assistant" role={big ? "dialog" : undefined} aria-modal={big || undefined}
             className={`card flex flex-col overflow-hidden !p-0 ${big
               ? "fixed inset-0 z-[70] !rounded-none lg:inset-auto lg:bottom-5 lg:right-5 lg:top-24 lg:w-[480px] lg:!rounded-3xl lg:shadow-[0_30px_80px_-24px_rgba(20,30,10,0.45)]"
               : ""}`}>
      <header className="flex items-start justify-between gap-3 border-b border-line bg-gradient-to-br from-signal-soft/70 via-paper to-paper px-5 py-4">
        <div className="flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-signal text-on-signal shadow-[0_8px_20px_-10px_rgb(var(--signal)/0.9)]">
            <Sparkles className="h-5 w-5" aria-hidden />
          </span>
          <div>
            <h2 className="text-[17px] font-bold leading-tight">Trip assistant</h2>
            <p className="mt-1 text-[13.5px] leading-snug text-ink-mute">
              {readOnly ? "This is a sample trip. Plan your own to chat about it."
                : "Ask about any stop and why it's famous, or tell it a change. Type or speak."}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 gap-1.5">
          <button type="button" onClick={() => { setSpeak(!speak); window.speechSynthesis?.cancel(); }} aria-pressed={speak}
                  title={speak ? "Spoken questions get spoken answers" : "Answers are text only"}
                  className="grid h-9 w-9 place-items-center rounded-full border border-line bg-paper text-ink-soft hover:border-ink/30">
            {speak ? <Volume2 className="h-4 w-4" aria-hidden /> : <VolumeX className="h-4 w-4" aria-hidden />}
            <span className="sr-only">{speak ? "Mute spoken answers" : "Speak answers"}</span>
          </button>
          <button type="button" onClick={() => setBig(!big)} title={big ? "Make smaller" : "Open large"}
                  className="grid h-9 w-9 place-items-center rounded-full border border-line bg-paper text-ink-soft hover:border-ink/30">
            {big ? <Minimize2 className="h-4 w-4" aria-hidden /> : <Maximize2 className="h-4 w-4" aria-hidden />}
            <span className="sr-only">{big ? "Make the chat smaller" : "Open the chat large"}</span>
          </button>
        </div>
      </header>

      <div ref={listRef} className={`relative space-y-4 overflow-y-auto px-5 py-5 ${big ? "min-h-0 flex-1" : "max-h-[30rem] min-h-[13rem]"}`} aria-live="polite">
        {msgs.length === 0 ? (
          <div className="space-y-4">
            <p className="flex items-start gap-2.5 rounded-2xl bg-signal-soft/60 px-3.5 py-3 text-[13.5px] leading-relaxed text-ink-soft">
              <MessageCircle className="mt-0.5 h-4 w-4 shrink-0 text-signal-deep" aria-hidden />
              <span>Tap the <span className="font-semibold text-ink">chat icon</span> on any stop in your plan to learn why it's famous, or ask here.</span>
            </p>
            {[["Ask", ASK], ["Change", chips.slice(0, 4)]].filter(([, list]) => list.length > 0).map(([label, list]) => (
              <div key={label as string}>
                <p className="mb-2 text-[11.5px] font-bold uppercase tracking-wider text-ink-mute">{label as string}</p>
                <div className="flex flex-wrap gap-2">
                  {(list as string[]).map((c) => (
                    <button key={c} type="button" className="chip-off !rounded-full !px-3 !py-1.5 text-[13.5px]" disabled={working} onClick={() => send(c)}>{c}</button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : msgs.map((m, i) => m.role === "user" ? (
          <div key={i} data-asked className="flex flex-col items-end gap-1">
            {m.about && (
              <span className="inline-flex max-w-[85%] items-center gap-1 truncate text-[12px] font-medium text-ink-mute">
                <MapPin className="h-3 w-3 shrink-0" aria-hidden />{m.about}
              </span>
            )}
            <p className="max-w-[85%] whitespace-pre-line rounded-2xl rounded-br-md bg-signal px-4 py-2.5 text-[14.5px] leading-relaxed text-on-signal">{m.text}</p>
          </div>
        ) : (
          <div key={i} className="flex items-start gap-2.5">
            {avatar}
            <div className="min-w-0 max-w-[88%]">
              <p className={`whitespace-pre-line rounded-2xl rounded-tl-md border border-line bg-haze/70 px-4 py-3 text-[14.5px] leading-relaxed text-ink ${m.pending ? "animate-pulse" : ""}`}>
                {m.text}
              </p>
              {m.source?.url && (
                <a href={m.source.url} target="_blank" rel="noopener noreferrer"
                   className="mt-1.5 inline-flex items-center gap-1.5 rounded-full border border-line bg-paper px-2.5 py-1 text-[12.5px] font-semibold text-ink-soft hover:border-ink/30 hover:text-ink">
                  <BookOpen className="h-3.5 w-3.5" aria-hidden />Read more on {m.source.label}<ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )}
            </div>
          </div>
        ))}
        {thinking && !msgs.some((m) => m.pending) && (
          <div className="flex items-start gap-2.5">
            {avatar}
            <p className="rounded-2xl rounded-tl-md border border-line bg-haze/70 px-4 py-3.5" aria-label="Thinking">
              <span className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-mute" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-mute [animation-delay:120ms]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-mute [animation-delay:240ms]" />
              </span>
            </p>
          </div>
        )}
      </div>

      <div className="border-t border-line bg-paper px-5 pb-4 pt-3">
        {focus && (
          <div className="mb-2 flex items-center gap-1.5">
            <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full bg-signal-soft px-3 py-1 text-[12.5px] font-semibold text-signal-deep">
              <MapPin className="h-3.5 w-3.5 shrink-0" aria-hidden /><span className="truncate">Asking about {focus.title}</span>
            </span>
            <button type="button" onClick={() => setFocus(null)} className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-ink-mute hover:bg-haze hover:text-ink">
              <X className="h-3.5 w-3.5" aria-hidden /><span className="sr-only">Stop asking about this stop</span>
            </button>
          </div>
        )}
        {msgs.length > 0 && !working && suggestions.length > 0 && (
          <div className="mb-2.5 flex gap-2 overflow-x-auto pb-1">
            {suggestions.map((c) => (
              <button key={c} type="button" className="chip-off shrink-0 whitespace-nowrap !rounded-full !px-3 !py-1.5 text-[13px]" onClick={() => send(c)}>{c}</button>
            ))}
          </div>
        )}
        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <label htmlFor={`chat-${tripId}`} className="sr-only">Message</label>
          <textarea id={`chat-${tripId}`} rows={1} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} disabled={working}
                    placeholder={listening ? "Listening…" : focus ? "Ask a follow-up…" : "Ask or change…"}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(text); } }}
                    className="field min-h-[48px] flex-1 resize-none !rounded-2xl !py-3 text-[14.5px]" />
          {canListen && (
            <button type="button" onClick={listen} disabled={working && !listening} aria-pressed={listening}
                    className={`grid h-12 w-12 shrink-0 place-items-center rounded-2xl border transition-colors ${
                      listening ? "border-danger bg-danger-soft text-danger" : "border-line bg-paper text-ink hover:border-signal/50"}`}>
              {listening ? <MicOff className="h-[18px] w-[18px]" aria-hidden /> : <Mic className="h-[18px] w-[18px]" aria-hidden />}
              <span className="sr-only">{listening ? "Stop listening" : "Speak"}</span>
            </button>
          )}
          <button type="submit" disabled={working || !text.trim()} className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-signal text-on-signal transition-opacity disabled:opacity-50">
            <Send className="h-[18px] w-[18px]" aria-hidden /><span className="sr-only">Send</span>
          </button>
        </form>
        <div className="mt-2.5 flex items-center justify-between text-[12px] text-ink-mute">
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
      </div>
    </section>
  );
  if (!big) return panel;
  // Large, it sits on the page itself (a parent's transform would otherwise pin a fixed panel inside the sidebar)
  return (
    <>
      <div className="card flex items-center justify-between gap-3 p-4 text-[14px] text-ink-soft">
        <span className="inline-flex items-center gap-2"><Sparkles className="h-4 w-4 text-signal-deep" aria-hidden />Trip assistant is open</span>
        <button type="button" onClick={() => setBig(false)} className="chip-off !rounded-full !px-3 !py-1 text-[13px]">Make smaller</button>
      </div>
      {createPortal(
        <>
          <div className="fixed inset-0 z-[65] bg-ink/20 backdrop-blur-[2px] lg:bg-transparent lg:backdrop-blur-0" onClick={() => setBig(false)} aria-hidden />
          {panel}
        </>, document.body)}
    </>
  );
}
