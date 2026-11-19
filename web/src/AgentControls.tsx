import { useState } from 'react'
import { request } from './api'
import type { BackgroundTask } from './types'

export function Clarification({ task }: { task: BackgroundTask }) {
  const [answer, setAnswer] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  if (task.status !== 'waiting_user' || !task.clarification) return null

  return <form className="clarification" onSubmit={async event => {
    event.preventDefault()
    setBusy(true)
    try {
      await request(`/api/tasks/${task.id}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer }),
      })
      setMessage('已继续处理')
      setAnswer('')
    } catch (error) { setMessage(String(error)) }
    finally { setBusy(false) }
  }}>
    <strong>还需要你确认一件事</strong>
    <p>{task.clarification.question}</p>
    <div className="tag-row">{task.clarification.choices.map(choice => <button type="button" key={choice} onClick={() => setAnswer(choice)}>{choice}</button>)}</div>
    <label>补充信息<input required maxLength={1000} value={answer} onChange={event => setAnswer(event.target.value)} /></label>
    <button disabled={busy || !answer.trim()}>继续补全</button>
    <p role="status">{message}</p>
    <small>等待期间不占处理名额。也可编辑产品，或上传更清楚的包装照片后重试。</small>
  </form>
}
