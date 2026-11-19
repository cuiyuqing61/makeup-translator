import type { BackgroundTask, Health, ProductRecord } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const payload = await response.json()
      message = payload.detail ?? message
    } catch {
      // Keep the HTTP status fallback when the server did not return JSON.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/api/health'),
  listProducts: () => request<ProductRecord[]>('/api/products'),
  createProduct: (input: {
    brand: string
    name: string
    model_or_shade?: string
    market?: string
  }) =>
    request<ProductRecord>('/api/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }),
  enrichProduct: (id: string, forceRefresh = false) =>
    request<BackgroundTask>(`/api/products/${id}/enrich?force_refresh=${forceRefresh}`, { method: 'POST' }),
  confirmProduct: (id: string, acknowledgeUnverified = false) =>
    request<ProductRecord>(`/api/products/${id}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ acknowledge_unverified: acknowledgeUnverified }),
    }),
  createTutorial: (form: FormData) =>
    request<BackgroundTask>('/api/tutorials', { method: 'POST', body: form }),
  listTasks: () => request<BackgroundTask[]>('/api/tasks'),
  getTask: (id: string) => request<BackgroundTask>(`/api/tasks/${id}`),
  cancelTask: (id: string) => request<BackgroundTask>(`/api/tasks/${id}`, { method: 'DELETE' }),
}

export { request }
