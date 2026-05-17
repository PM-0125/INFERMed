import type { AnalyzeRequest, FollowUpRequest, FollowUpResponse, InteractionResult } from './types'

const API_URL = import.meta.env.VITE_INFERMED_API_URL?.replace(/\/$/, '')

export async function analyzeInteraction(request: AnalyzeRequest): Promise<InteractionResult> {
  if (!API_URL) {
    throw new Error('Backend API URL is not configured. Set VITE_INFERMED_API_URL and start the FastAPI service.')
  }

  const response = await fetch(`${API_URL}/api/interactions/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`Analyze request failed: ${response.status}`)
  }

  return response.json()
}

export async function askFollowUp(request: FollowUpRequest): Promise<FollowUpResponse> {
  if (!API_URL) {
    throw new Error('Backend API URL is not configured. Set VITE_INFERMED_API_URL and start the FastAPI service.')
  }

  const response = await fetch(`${API_URL}/api/interactions/followup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`Follow-up request failed: ${response.status}`)
  }

  return response.json()
}
