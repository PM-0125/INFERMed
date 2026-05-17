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
}

export interface AnalyzeRequest {
  drugs: string[]
  mode: AudienceMode
  refreshEvidence?: boolean
}

export interface FollowUpRequest {
  question: string
  drugs: string[]
  mode: AudienceMode
  contextId?: string
  history?: Array<{ role: 'user' | 'assistant'; text: string }>
  priorAssessment?: string
  followUpCount?: number
}

export interface FollowUpResponse {
  answer: string
  citedCards: string[]
}
