import { useEffect, useRef, useState } from 'react'
import { useChat, type Region } from './hooks/useChat'
import { ChatMessage } from './components/ChatMessage'
import { ChatInput } from './components/ChatInput'

const SESSION_ID = `session-${Math.random().toString(36).slice(2, 10)}`

const REGIONS: { value: Region; label: string }[] = [
  { value: 'US', label: 'US' },
  { value: 'IN', label: 'India' },
  { value: 'BOTH', label: 'Global' },
]

function LogoMark({ className = 'w-9 h-9' }: { className?: string }) {
  return (
    <div className={`${className} rounded-xl bg-linear-to-br from-emerald-400 via-emerald-500 to-teal-600 flex items-center justify-center shadow-lg shadow-emerald-950/60 ring-1 ring-white/10`}>
      <svg viewBox="0 0 24 24" className="w-[55%] h-[55%] fill-white">
        <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V6h16v12zM6 10h2v2H6zm0 4h8v2H6zm10 0h2v2h-2zm-6-4h8v2h-8z"/>
      </svg>
    </div>
  )
}

export default function App() {
  const [region, setRegion] = useState<Region>('US')
  const { messages, isLoading, sendMessage, stopStreaming, clearChat } = useChat(SESSION_ID, region)
  const bottomRef = useRef<HTMLDivElement>(null)
  const isEmpty = messages.length === 0

  // Auto-scroll to bottom as tokens arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="flex flex-col h-dvh bg-zinc-950 text-zinc-100 relative overflow-hidden">

      {/* Ambient top glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 left-1/2 -translate-x-1/2 w-[700px] h-[350px] rounded-full opacity-20"
        style={{ background: 'radial-gradient(ellipse at center, rgba(16,185,129,0.5), transparent 65%)' }}
      />

      {/* ── Header ── */}
      <header className="relative z-10 flex items-center justify-between gap-2 px-3 sm:px-8 py-3.5 border-b border-white/5 bg-zinc-950/70 backdrop-blur-xl shrink-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <LogoMark className="w-9 h-9 shrink-0" />
          <div className="min-w-0">
            <h1 className="text-[15px] font-semibold tracking-tight text-white leading-none">CardAI</h1>
            <p className="hidden sm:block text-[11px] text-zinc-500 mt-1 leading-none">Credit Card Intelligence</p>
          </div>
        </div>

        <div className="flex items-center gap-2 sm:gap-3 shrink-0">
          {/* Region selector — segmented control */}
          <div className="flex items-center gap-0.5 rounded-full bg-zinc-900 border border-white/5 p-0.5 shadow-inner shadow-black/40">
            {REGIONS.map(r => (
              <button
                key={r.value}
                onClick={() => setRegion(r.value)}
                aria-pressed={region === r.value}
                className={`text-xs font-medium px-2.5 sm:px-3.5 py-1.5 rounded-full transition-all duration-200 ${
                  region === r.value
                    ? 'bg-zinc-800 text-emerald-300 shadow-sm ring-1 ring-white/10'
                    : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>

          {/* Status */}
          <span className="hidden md:flex items-center gap-1.5 text-[11px] font-medium text-zinc-500 border border-white/5 bg-zinc-900/60 px-2.5 py-1.5 rounded-full">
            <span className="relative flex w-1.5 h-1.5">
              <span className="absolute inline-flex w-full h-full rounded-full bg-emerald-500 opacity-60 animate-ping" />
              <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-emerald-500" />
            </span>
            Live data
          </span>

          {messages.length > 0 && (
            <button
              onClick={clearChat}
              title="New chat"
              className="flex items-center justify-center text-xs font-medium text-zinc-400 hover:text-white bg-zinc-900 hover:bg-zinc-800 border border-white/5 w-8 h-8 sm:w-auto sm:h-auto sm:px-3.5 sm:py-1.5 rounded-full transition-all duration-200"
            >
              {/* Icon on phones, label on larger screens */}
              <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current sm:hidden">
                <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
              </svg>
              <span className="hidden sm:inline">New chat</span>
            </button>
          )}
        </div>
      </header>

      {/* ── Chat area ── */}
      {/* overscroll-contain keeps message scrolling from chaining to the page on touch devices */}
      <main className="relative z-10 flex-1 overflow-y-auto overscroll-contain">
        {isEmpty ? (
          <WelcomeScreen onSend={sendMessage} region={region} />
        ) : (
          <div className="max-w-3xl mx-auto py-8 px-1">
            {messages.map(msg => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            <div ref={bottomRef} className="h-2" />
          </div>
        )}
      </main>

      {/* ── Input ── */}
      <div className="relative z-10 shrink-0 max-w-3xl w-full mx-auto">
        <ChatInput
          onSend={sendMessage}
          onStop={stopStreaming}
          isLoading={isLoading}
          region={region}
          showSuggestions={isEmpty}
        />
      </div>
    </div>
  )
}

/* ─── Welcome / empty state ─── */

const EXAMPLES: Record<Region, { label: string; query: string; icon: string }[]> = {
  US: [
    { label: 'No annual fee', query: 'Which US cards have no annual fee?', icon: '💳' },
    { label: 'Airport lounges', query: 'What is the best US card for airport lounge access?', icon: '✈️' },
    { label: 'Dining rewards', query: 'Which card gives the most rewards on dining and restaurants?', icon: '🍽️' },
    { label: 'Travel bonus', query: 'Show me cards with the best travel signup bonuses over $500 value', icon: '🌍' },
    { label: 'Low APR', query: 'What cards have the lowest APR under 22%?', icon: '📉' },
    { label: 'Compare cards', query: 'Compare the Sapphire Preferred vs Sapphire Reserve for a frequent traveler', icon: '⚖️' },
  ],
  IN: [
    { label: 'Travel cards', query: 'Best travel credit cards in India with lounge access', icon: '✈️' },
    { label: 'LTF cards', query: 'Which lifetime free credit cards are best in India?', icon: '💳' },
    { label: 'Fuel cards', query: 'Best fuel credit card in India with surcharge waiver', icon: '⛽' },
    { label: 'HDFC vs ICICI', query: 'Compare HDFC Regalia Gold vs ICICI Sapphiro', icon: '⚖️' },
    { label: 'Milestone perks', query: 'Indian credit cards with the best milestone benefits', icon: '🎯' },
    { label: 'Cashback', query: 'Best cashback credit cards in India for online shopping', icon: '🛒' },
  ],
  BOTH: [
    { label: 'Global lounges', query: 'Compare US and India credit cards for airport lounge access', icon: '✈️' },
    { label: 'Global travel', query: 'Best global travel credit card for someone who flies internationally', icon: '🌍' },
    { label: 'No annual fee', query: 'Best no annual fee cards in the US and India', icon: '💳' },
    { label: 'Dining rewards', query: 'Top dining rewards cards across US and India', icon: '🍽️' },
    { label: 'Premium cards', query: 'Compare premium cards: Amex Platinum vs HDFC Diners Club Black', icon: '👑' },
    { label: 'Cashback', query: 'Best flat-rate cashback cards in the US and India', icon: '🛒' },
  ],
}

function WelcomeScreen({ onSend, region }: { onSend: (t: string) => void; region: Region }) {
  const examples = EXAMPLES[region]
  const regionLabel = region === 'US' ? 'US' : region === 'IN' ? 'Indian' : 'US & Indian'

  return (
    <div className="flex flex-col items-center justify-center min-h-full px-4 py-12 animate-fade-in">
      {/* Hero */}
      <div className="animate-scale-in">
        <LogoMark className="w-14 h-14 mx-auto mb-7" />
      </div>

      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-emerald-400/90 mb-3 animate-fade-up" style={{ animationDelay: '0.05s' }}>
        Live {regionLabel} card data
      </span>

      <h2 className="text-[28px] sm:text-[32px] font-bold tracking-tight text-white mb-3 text-center animate-fade-up" style={{ animationDelay: '0.1s' }}>
        What card fits your life?
      </h2>
      <p className="text-zinc-400 text-sm leading-relaxed mb-10 text-center max-w-md animate-fade-up" style={{ animationDelay: '0.15s' }}>
        Ask about fees, rewards, APRs, lounge access, or signup bonuses —
        answers are grounded in live web data, never guesses.
      </p>

      {/* Example cards */}
      <div className="stagger grid grid-cols-2 md:grid-cols-3 gap-2.5 w-full max-w-2xl">
        {examples.map(ex => (
          <button
            key={ex.label}
            onClick={() => onSend(ex.query)}
            className="group flex flex-col items-start gap-2 bg-zinc-900/60 border border-white/5 hover:border-emerald-500/40 hover:bg-zinc-900 rounded-2xl px-4 py-3.5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-emerald-950/30"
          >
            <span className="text-lg leading-none">{ex.icon}</span>
            <span className="text-xs font-semibold text-zinc-300 group-hover:text-emerald-300 transition-colors duration-200">
              {ex.label}
            </span>
            <span className="text-[11px] text-zinc-500 leading-snug line-clamp-2">{ex.query}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
