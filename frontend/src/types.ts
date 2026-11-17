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
  verification?: {
    status: 'checks_passed' | 'review_required' | 'failed' | 'recommendations_withheld' | 'review_failed'
    publication?: {status: string; withheld_count: number; source_matched_count: number; limitation: string}
    claims?: Array<{text: string; evidence_ids: string[]; source_labels?: string[]; status: string; reasons: string[]; claim_kind?: string; verification_method?: string; patient_applicability?: {status: string; required_fields: string[]; reasons: string[]; limitation: string; population_differences?: string[]}; source_matches?: Array<{source_name: string; section: string; text: string; context?: string; source_url?: string | null}>}>
    notes?: string[]
  }
  evidenceFreshness?: Array<{pair: string[]; assembledAt?: string; cacheStatus: string}>
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
  type: 'progress' | 'token' | 'result' | 'error'
  text?: string
  stage?: string
  message?: string
  payload?: Record<string, unknown>
  result?: InteractionResult
  detail?: string
}
