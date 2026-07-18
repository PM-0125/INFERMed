import type { AnalysisProgressEvent, AnalyzeRequest, FollowUpRequest, FollowUpResponse, InteractionResult } from './types'

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

  return normalizeInteractionResult(await response.json())
}

export async function analyzeInteractionStream(
  request: AnalyzeRequest,
  onEvent: (event: AnalysisProgressEvent) => void,
): Promise<InteractionResult> {
  if (!API_URL) {
    throw new Error('Backend API URL is not configured. Set VITE_INFERMED_API_URL and start the FastAPI service.')
  }

  const response = await fetch(`${API_URL}/api/medication-sets/analyze/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      medications: request.drugs.map(text => ({ text })),
      audience: request.mode,
      patient_context: request.patient_context,
      refresh_evidence: Boolean(request.refreshEvidence),
      analysis_depth: 'standard',
    }),
  })

  if (!response.ok || !response.body) {
    throw new Error(`Analyze stream failed: ${response.status}`)
  }

  const decoder = new TextDecoder()
  const reader = response.body.getReader()
  let buffer = ''
  let result: InteractionResult | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const event = parseSseChunk(chunk)
      if (!event) continue
      onEvent(event)
      if (event.type === 'error') {
        throw new Error(event.detail || 'Analysis stream failed.')
      }
      if (event.type === 'result' && event.result) {
        result = normalizeInteractionResult(event.result)
      }
    }
  }

  if (!result) {
    throw new Error('Analysis stream ended without a result.')
  }
  return result
}

function normalizeInteractionResult(raw: InteractionResult): InteractionResult {
  const compatibility = (raw as any)?.compatibility
  const display = compatibility && !raw?.assessment ? compatibility : raw
  const evidence = display?.evidence ?? ({} as InteractionResult['evidence'])
  return {
    ...raw,
    drugs: asArray(display?.drugs).map(drug => ({
      name: asString(drug?.name, 'Unknown drug'),
      pubchemCid: asOptionalString(drug?.pubchemCid),
      drugbankId: asOptionalString(drug?.drugbankId),
      aliases: asArray(drug?.aliases).map(item => asString(item)),
    })),
    risk: {
      level: coerceRisk(display?.risk?.level),
      label: asString(display?.risk?.label, 'Unknown risk'),
      interactionClass: asString(display?.risk?.interactionClass, 'Drug-drug interaction'),
      confidence: asString(display?.risk?.confidence, 'unknown'),
    },
    generatedAt: asString(display?.generatedAt, new Date().toISOString()),
    sourceBadges: asArray(display?.sourceBadges).map(item => asString(item)).filter(Boolean),
    assessment: normalizeAssessment(display?.assessment),
    evidence: {
      overview: normalizeEvidenceCard(evidence.overview),
      openfda: {
        ...normalizeEvidenceCard(evidence.openfda),
        caveat: asString(evidence.openfda?.caveat),
      },
      internal: normalizeEvidenceCard(evidence.internal),
      mechanisms: normalizeEvidenceCard(evidence.mechanisms),
      sources: asArray(evidence.sources).map(src => ({
        name: asString(src?.name, 'Unknown source'),
        state: src?.state === 'disabled' || src?.state === 'unavailable' ? src.state : 'active',
        detail: asString(src?.detail),
      })),
      references: normalizeRows(evidence.references),
    },
    executedPairs: asArray(raw?.executedPairs).map(pair => asArray(pair).map(item => asString(item)).filter(Boolean)),
    pairResults: asArray(raw?.pairResults),
    ndrugReasoning: normalizeReasoning(raw?.ndrugReasoning),
  }
}

function normalizeAssessment(value: unknown): InteractionResult['assessment'] {
  const rows = asArray(value).map(section => ({
    title: asString(section?.title, 'Assessment'),
    body: asString(section?.body),
  })).filter(section => section.title || section.body)
  return rows.length ? rows : [{ title: 'Assessment', body: 'No assessment text was returned.' }]
}

function normalizeEvidenceCard(card: unknown): { metrics: Array<{ label: string; value: string; tone?: any }>; rows: Array<{ title: string; description: string; meta?: string }> } {
  return {
    metrics: asArray((card as any)?.metrics).map(metric => ({
      label: asString(metric?.label, 'Metric'),
      value: asString(metric?.value, 'Unknown'),
      tone: metric?.tone,
    })),
    rows: normalizeRows((card as any)?.rows),
  }
}

function normalizeRows(value: unknown): Array<{ title: string; description: string; meta?: string }> {
  return asArray(value).map(row => ({
    title: asString(row?.title, 'Evidence'),
    description: asString(row?.description),
    meta: asOptionalString(row?.meta),
  }))
}

function normalizeReasoning(value: InteractionResult['ndrugReasoning']): InteractionResult['ndrugReasoning'] {
  if (!value) return undefined
  return {
    pair_count: typeof value.pair_count === 'number' ? value.pair_count : undefined,
    top_pairs: asArray(value.top_pairs).map(pair => ({
      ...pair,
      pair: asArray(pair?.pair).map(item => asString(item)).filter(Boolean),
    })),
    clusters: asArray(value.clusters),
    evidence_gaps: value.evidence_gaps ?? {},
    hypotheses: asArray(value.hypotheses),
    research_signals: asArray(value.research_signals),
  }
}

function asArray<T = any>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : []
}

function asString(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback
  return String(value)
}

function asOptionalString(value: unknown): string | undefined {
  const text = asString(value).trim()
  return text || undefined
}

function coerceRisk(value: unknown): InteractionResult['risk']['level'] {
  return value === 'low' || value === 'moderate' || value === 'high' ? value : 'unknown'
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

function parseSseChunk(chunk: string): AnalysisProgressEvent | null {
  const dataLine = chunk
    .split('\n')
    .map(line => line.trim())
    .find(line => line.startsWith('data:'))
  if (!dataLine) return null
  try {
    return JSON.parse(dataLine.slice(5).trim()) as AnalysisProgressEvent
  } catch {
    return null
  }
}
