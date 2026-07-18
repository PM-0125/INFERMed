export type AudienceMode = 'doctor' | 'patient' | 'pv_research'

export type RiskLevel = 'unknown' | 'low' | 'moderate' | 'high'

export type SourceState = 'active' | 'disabled' | 'unavailable'

export interface DrugIdentity {
  name: string
  pubchemCid?: string
  drugbankId?: string
  aliases?: string[]
}

export interface RiskSummary {
  level: RiskLevel
  label: string
  interactionClass: string
  confidence: string
}

export interface SourceStatus {
  name: string
  state: SourceState
  detail: string
}

export interface EvidenceMetric {
  label: string
  value: string
  tone?: RiskLevel | 'neutral'
}

export interface EvidenceRow {
  title: string
  description: string
  meta?: string
}

export interface EvidenceBundle {
  overview: {
    metrics: EvidenceMetric[]
    rows: EvidenceRow[]
  }
  openfda: {
    metrics: EvidenceMetric[]
    rows: EvidenceRow[]
    caveat: string
  }
  internal: {
    metrics: EvidenceMetric[]
    rows: EvidenceRow[]
  }
  mechanisms: {
    metrics: EvidenceMetric[]
    rows: EvidenceRow[]
  }
  sources: SourceStatus[]
  references: EvidenceRow[]
}

export interface AssessmentSection {
  title: string
  body: string
}

export interface InteractionResult {
  drugs: DrugIdentity[]
  risk: RiskSummary
  generatedAt: string
  sourceBadges: string[]
  assessment: AssessmentSection[]
  evidence: EvidenceBundle
  analysisId?: string
  executedPairs?: string[][]
  pairResults?: Array<{
    pair: string[]
    decision?: {
      risk_level: string
      confidence: string
      evidence_grade: string
    }
    evidence_card_count?: number
  }>
  ndrugReasoning?: {
    pair_count?: number
    top_pairs?: Array<{ pair?: string[]; risk_level?: string; confidence?: string; evidence_grade?: string }>
    clusters?: Array<{ label?: string; risk_type?: string; confidence?: string; affected_drugs?: string[] }>
    evidence_gaps?: Record<string, string[]>
    hypotheses?: Array<{ statement?: string; support_level?: string; affected_drugs?: string[] }>
    research_signals?: Array<{ label?: string; support?: string; source_names?: string[]; limitations?: string[] }>
  }
}

export interface AnalyzeRequest {
  drugs: string[]
  mode: AudienceMode
  refreshEvidence?: boolean
  patient_context?: Record<string, unknown>
}

export interface FollowUpRequest {
  question: string
  drugs: string[]
  mode: AudienceMode
  contextId?: string
  patient_context?: Record<string, unknown>
  history?: Array<{ role: 'user' | 'assistant'; text: string }>
  priorAssessment?: string
  followUpCount?: number
}

export interface FollowUpResponse {
  answer: string
  citedCards: string[]
}

export interface AnalysisProgressEvent {
  type: 'progress' | 'result' | 'error'
  stage?: string
  message?: string
  payload?: Record<string, unknown>
  result?: InteractionResult
  detail?: string
}
