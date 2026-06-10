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
      <div className="flex justify-end mb-4 px-4">
        <div className="max-w-[75%]">
          <div className="bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm leading-relaxed shadow-lg">
            {message.content}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start mb-4 px-4">
      {/* Avatar */}
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center mr-3 mt-0.5 shadow-md">
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-white" xmlns="http://www.w3.org/2000/svg">
          <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V6h16v12zM6 10h2v2H6zm0 4h8v2H6zm10 0h2v2h-2zm-6-4h8v2h-8z"/>
        </svg>
      </div>

      {/* Bubble */}
      <div className="max-w-[80%]">
        <div
          className={`bg-slate-800 border rounded-2xl rounded-tl-sm px-4 py-3 shadow-lg ${
            message.isError
              ? 'border-red-500/40 text-red-400'
              : 'border-slate-700/60 text-slate-300'
          } ${message.isStreaming && !message.content ? 'min-w-[60px]' : ''}`}
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
    <div className="flex items-center gap-1 py-1 px-1">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="w-2 h-2 rounded-full bg-slate-400 inline-block"
          style={{ animation: `blink 1.2s ${i * 0.2}s ease-in-out infinite` }}
        />
      ))}
    </div>
  )
}
