import { useState, useCallback, useRef } from 'react'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  isStreaming?: boolean
  isError?: boolean
}

export type Region = 'US' | 'IN' | 'BOTH'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const API_URL = `${API_BASE}/api/chat`

function uid() {
  return Math.random().toString(36).slice(2, 10)
}

export function useChat(sessionId: string, region: Region = 'US') {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || isLoading) return

    const userMsg: Message = { id: uid(), role: 'user', content: content.trim() }
    const assistantId = uid()
    const assistantMsg: Message = { id: assistantId, role: 'assistant', content: '', isStreaming: true }

    setMessages(prev => [...prev, userMsg, assistantMsg])
    setIsLoading(true)

    abortRef.current = new AbortController()

    // Build history from current messages + new user message for the API
    const history = [...messages, userMsg].map(m => ({ role: m.role, content: m.content }))

    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history, session_id: sessionId, region }),
        signal: abortRef.current.signal,
      })

      if (!response.ok || !response.body) {
        throw new Error(`Server error: ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value, { stream: true })
        const lines = chunk.split('\n')

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith('data: ')) continue

          const payload = trimmed.slice(6)
          if (payload === '[DONE]') break

          try {
            const parsed = JSON.parse(payload)
            if (parsed.token) {
              accumulated += parsed.token
              setMessages(prev =>
                prev.map(m =>
                  m.id === assistantId ? { ...m, content: accumulated } : m
                )
              )
            }
            // heartbeat = server is retrying after a 429, ignore silently
            if (parsed.heartbeat) continue
            if (parsed.error) {
              throw new Error(parsed.error)
            }
          } catch (e) {
            if ((e as Error).message !== 'Unexpected token') {
              throw e
            }
          }
        }
      }

      // Mark streaming complete
      setMessages(prev =>
        prev.map(m => m.id === assistantId ? { ...m, isStreaming: false } : m)
      )
    } catch (err) {
      if ((err as Error).name === 'AbortError') return

      const errorText = (err as Error).message || 'Something went wrong. Please try again.'
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantId
            ? { ...m, content: errorText, isStreaming: false, isError: true }
            : m
        )
      )
    } finally {
      setIsLoading(false)
    }
  }, [messages, isLoading, sessionId, region])

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort()
    setIsLoading(false)
    setMessages(prev =>
      prev.map(m => m.isStreaming ? { ...m, isStreaming: false } : m)
    )
  }, [])

  const clearChat = useCallback(() => {
    stopStreaming()
    setMessages([])
  }, [stopStreaming])

  return { messages, isLoading, sendMessage, stopStreaming, clearChat }
}
