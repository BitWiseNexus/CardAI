import { useEffect, useRef } from 'react'
import { useChat } from './hooks/useChat'
import { ChatMessage } from './components/ChatMessage'
import { ChatInput } from './components/ChatInput'

const SESSION_ID = `session-${Math.random().toString(36).slice(2, 10)}`

export default function App() {
  const { messages, isLoading, sendMessage, stopStreaming, clearChat } = useChat(SESSION_ID)
  const bottomRef = useRef<HTMLDivElement>(null)
  const isEmpty = messages.length === 0

  // Auto-scroll to bottom as tokens arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="flex flex-col h-screen bg-slate-950 text-slate-100">

      {/* ── Header ── */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-slate-800 flex-shrink-0">
        <div className="flex items-center gap-3">
          {/* Logo mark */}
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-lg shadow-indigo-900/40">
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-white">
              <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V6h16v12zM6 10h2v2H6zm0 4h8v2H6zm10 0h2v2h-2zm-6-4h8v2h-8z"/>
            </svg>
          </div>
          <div>
            <h1 className="text-base font-semibold text-white leading-none">CardAI</h1>
            <p className="text-xs text-slate-500 mt-0.5">Credit Card Advisor</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Status dot */}
          <span className="flex items-center gap-1.5 text-xs text-slate-500">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Live
          </span>

          {messages.length > 0 && (
            <button
              onClick={clearChat}
              className="ml-2 text-xs text-slate-500 hover:text-slate-300 border border-slate-700 hover:border-slate-500 px-3 py-1.5 rounded-lg transition-colors"
            >
              New chat
            </button>
          )}
        </div>
      </header>

      {/* ── Chat area ── */}
      <main className="flex-1 overflow-y-auto">
        {isEmpty ? (
          <WelcomeScreen onSend={sendMessage} />
        ) : (
          <div className="max-w-3xl mx-auto py-6">
            {messages.map(msg => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </main>

      {/* ── Input ── */}
      <div className="flex-shrink-0 max-w-3xl w-full mx-auto">
        <ChatInput onSend={sendMessage} onStop={stopStreaming} isLoading={isLoading} />
      </div>
    </div>
  )
}

/* ─── Welcome / empty state ─── */
function WelcomeScreen({ onSend }: { onSend: (t: string) => void }) {
  const examples = [
    { label: 'No annual fee', query: 'Which Chase cards have no annual fee?', icon: '💳' },
    { label: 'Airport lounges', query: 'What is the best card for airport lounge access?', icon: '✈️' },
    { label: 'Dining rewards', query: 'Which card gives the most rewards on dining and restaurants?', icon: '🍽️' },
    { label: 'Travel bonus', query: 'Show me cards with the best travel signup bonuses over $500 value', icon: '🌍' },
    { label: 'Low APR', query: 'What cards have the lowest APR under 22%?', icon: '📉' },
    { label: 'Compare cards', query: 'Compare the Sapphire Preferred vs Sapphire Reserve for a frequent traveler', icon: '⚖️' },
  ]

  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-12">
      {/* Hero */}
      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-2xl shadow-indigo-900/50 mb-6">
        <svg viewBox="0 0 24 24" className="w-8 h-8 fill-white">
          <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V6h16v12zM6 10h2v2H6zm0 4h8v2H6zm10 0h2v2h-2zm-6-4h8v2h-8z"/>
        </svg>
      </div>
      <h2 className="text-2xl font-bold text-white mb-2">What card fits your life?</h2>
      <p className="text-slate-400 text-sm mb-10 text-center max-w-sm">
        Ask about fees, rewards, APRs, lounge access, or signup bonuses — I'll find the best match from live data.
      </p>

      {/* Example cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 w-full max-w-2xl">
        {examples.map(ex => (
          <button
            key={ex.label}
            onClick={() => onSend(ex.query)}
            className="flex flex-col items-start gap-2 bg-slate-900 border border-slate-800 hover:border-indigo-500 hover:bg-slate-800 rounded-xl px-4 py-3 text-left transition-all group"
          >
            <span className="text-xl">{ex.icon}</span>
            <span className="text-xs font-medium text-slate-400 group-hover:text-indigo-400 transition-colors">{ex.label}</span>
            <span className="text-xs text-slate-600 leading-snug">{ex.query}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
