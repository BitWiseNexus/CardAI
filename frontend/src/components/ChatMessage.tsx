import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../hooks/useChat'

interface Props {
  message: Message
}

export function ChatMessage({ message }: Props) {
  const isUser = message.role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end mb-5 px-4 animate-fade-up">
        <div className="max-w-[78%]">
          <div className="bg-linear-to-br from-emerald-600 to-teal-700 text-white rounded-2xl rounded-br-md px-4 py-2.5 text-sm leading-relaxed shadow-lg shadow-emerald-950/40 ring-1 ring-white/10">
            {message.content}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start mb-5 px-4 animate-fade-up">
      {/* Avatar */}
      <div className="shrink-0 w-7 h-7 rounded-lg bg-linear-to-br from-emerald-400 to-teal-600 flex items-center justify-center mr-3 mt-1 shadow-md shadow-emerald-950/50 ring-1 ring-white/10">
        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-white" xmlns="http://www.w3.org/2000/svg">
          <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V6h16v12zM6 10h2v2H6zm0 4h8v2H6zm10 0h2v2h-2zm-6-4h8v2h-8z"/>
        </svg>
      </div>

      {/* Bubble */}
      <div className="max-w-[82%] min-w-0">
        <div
          className={`rounded-2xl rounded-tl-md px-4 py-3 transition-colors duration-200 ${
            message.isError
              ? 'bg-rose-950/40 border border-rose-500/30 text-rose-300'
              : 'bg-zinc-900/80 border border-white/5 text-zinc-300'
          } ${message.isStreaming && !message.content ? 'min-w-16' : ''}`}
        >
          {message.content === '' && message.isStreaming ? (
            /* Typing dots while waiting for first token */
            <TypingDots />
          ) : (
            <div className={`prose text-sm ${message.isStreaming ? 'streaming-cursor' : ''}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1.5 py-1.5 px-1" aria-label="Assistant is thinking">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-emerald-400/80 inline-block"
          style={{ animation: `blink 1.2s ${i * 0.18}s ease-in-out infinite` }}
        />
      ))}
    </div>
  )
}
