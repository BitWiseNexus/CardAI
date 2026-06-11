import { useState, useRef, type KeyboardEvent } from 'react'
import type { Region } from '../hooks/useChat'

interface Props {
  onSend: (text: string) => void
  onStop: () => void
  isLoading: boolean
  region?: Region
  showSuggestions?: boolean
}

const SUGGESTIONS: Record<Region, string[]> = {
  US: [
    'No annual fee cards',
    'Best for airport lounges',
    'Highest dining rewards',
    'Best signup bonus',
  ],
  IN: [
    'Best travel cards India',
    'LTF cards India',
    'Best fuel card India',
    'HDFC vs ICICI',
  ],
  BOTH: [
    'Compare US and India lounge cards',
    'Best global travel card',
  ],
}

export function ChatInput({ onSend, onStop, isLoading, region = 'US', showSuggestions = true }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = () => {
    const trimmed = value.trim()
    if (!trimmed || isLoading) return
    onSend(trimmed)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }

  const suggestions = SUGGESTIONS[region]

  return (
    <div className="px-4 pt-2 pb-4">
      {/* Quick suggestion chips — only while the chat is empty */}
      {showSuggestions && (
        <div className="flex gap-2 mb-3 flex-wrap justify-center animate-fade-in">
          {suggestions.map(s => (
            <button
              key={s}
              onClick={() => onSend(s)}
              disabled={isLoading}
              className="text-xs font-medium px-3.5 py-1.5 rounded-full bg-zinc-900/70 border border-white/5 text-zinc-400 hover:border-emerald-500/40 hover:text-emerald-300 hover:bg-zinc-900 transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-2.5 bg-zinc-900 border border-white/10 rounded-2xl pl-4 pr-2.5 py-2.5 shadow-xl shadow-black/40 transition-all duration-200 focus-within:border-emerald-500/50 focus-within:shadow-emerald-950/20">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder="Ask about credit cards — fees, rewards, travel perks…"
          rows={1}
          disabled={isLoading}
          className="flex-1 bg-transparent text-zinc-200 text-sm placeholder-zinc-500 resize-none outline-none leading-relaxed py-1 disabled:opacity-60"
        />

        {isLoading ? (
          <button
            onClick={onStop}
            className="shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white ring-1 ring-white/10 transition-all duration-200"
            title="Stop generating"
          >
            <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!value.trim()}
            className="shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-linear-to-br from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 disabled:opacity-30 disabled:cursor-not-allowed text-white shadow-lg shadow-emerald-950/40 transition-all duration-200 active:scale-95"
            title="Send (Enter)"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
          </button>
        )}
      </div>

      <p className="text-center text-[11px] text-zinc-600 mt-2.5">
        Shift+Enter for new line · Enter to send · Answers grounded in live card data
      </p>
    </div>
  )
}
