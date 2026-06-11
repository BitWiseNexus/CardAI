import { useState, useRef, type KeyboardEvent } from 'react'
import type { Region } from '../hooks/useChat'

interface Props {
  onSend: (text: string) => void
  onStop: () => void
  isLoading: boolean
  region?: Region
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

export function ChatInput({ onSend, onStop, isLoading, region = 'US' }: Props) {
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
    <div className="border-t border-slate-800 bg-slate-950 px-4 pt-3 pb-4">
      {/* Quick suggestion chips — only show when chat is empty */}
      <div className="flex gap-2 mb-3 flex-wrap justify-center">
        {suggestions.map(s => (
          <button
            key={s}
            onClick={() => onSend(s)}
            disabled={isLoading}
            className="text-xs px-3 py-1.5 rounded-full border border-slate-700 text-slate-400 hover:border-indigo-500 hover:text-indigo-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {s}
          </button>
        ))}
      </div>

      {/* Input row */}
      <div className="flex items-end gap-3 bg-slate-900 border border-slate-700 rounded-2xl px-4 py-3 focus-within:border-indigo-500 transition-colors">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder="Ask about credit cards — fees, rewards, travel perks…"
          rows={1}
          disabled={isLoading}
          className="flex-1 bg-transparent text-slate-200 text-sm placeholder-slate-500 resize-none outline-none leading-relaxed disabled:opacity-60"
        />

        {isLoading ? (
          <button
            onClick={onStop}
            className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-red-500/20 hover:bg-red-500/30 text-red-400 transition-colors"
            title="Stop generating"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!value.trim()}
            className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-30 disabled:cursor-not-allowed text-white transition-colors"
            title="Send (Enter)"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
          </button>
        )}
      </div>

      <p className="text-center text-xs text-slate-600 mt-2">
        Shift+Enter for new line · Enter to send
      </p>
    </div>
  )
}
