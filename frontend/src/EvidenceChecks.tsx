import type { InteractionResult } from './types'

type Claim = NonNullable<NonNullable<InteractionResult['verification']>['claims']>[number]
const applicabilityLabels: Record<string, string> = {
  not_established: 'Not established', missing_context: 'Missing information',
  condition_conflict: 'Condition conflicts with patient data', conditions_matched: 'Recognized conditions match',
  study_population_only: 'Study population only — not a patient prescription',
}
const fieldLabel = (value: string) => value === 'creatinine_clearance_ml_min' ? 'measured creatinine clearance (mL/min)' : value.replaceAll('_', ' ')
function supportLabel(claim: Claim) {
  if (claim.status !== 'supported') return claim.status === 'unresolved' ? 'Not established by automated check' : claim.status
  if (claim.verification_method === 'literal_patient_field') return 'Supplied patient field'
  return claim.verification_method === 'structured_relation' ? 'Structured relationship matched'
    : claim.verification_method === 'study_regimen_equivalence' ? 'Study details matched' : 'Source-matched'
}

function safeSourceUrl(value?: string | null) {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}

export function EvidenceChecks({ claims = [] }: { claims?: Claim[] }) {
  if (!claims.length) return null
  const patientMatched = claims.filter(c => c.status === 'supported' && c.verification_method === 'literal_patient_field').length
  const supported = claims.filter(c => c.status === 'supported' && c.verification_method !== 'literal_patient_field').length
  return <details>
    <summary>Inspect evidence checks — {supported} source-supported, {patientMatched} patient-input matched, {claims.length - supported - patientMatched} need review</summary>
    <p>Checks below refer to the published text. Source matching does not establish clinical appropriateness. Withheld draft recommendations remain in the audit record.</p>
    <ul>{claims.map((claim, index) => <li key={index}>
      <strong>{supportLabel(claim)}: </strong>{claim.text}
      <p>{claim.reasons.join(' ')} {(claim.source_labels ?? claim.evidence_ids).join('; ')}</p>
      {claim.claim_kind === 'study_observation' && <p>Study observation — the reported regimen is not prescribing advice.</p>}
      {claim.patient_applicability && <div>
        <strong>Patient applicability: {applicabilityLabels[claim.patient_applicability.status] ?? 'Not established'}</strong>
        <p>{claim.patient_applicability.reasons?.join(' ')}</p>
        {!!claim.patient_applicability.required_fields?.length && <p>Missing: {claim.patient_applicability.required_fields.map(fieldLabel).join(', ')}</p>}
        {!!claim.patient_applicability.population_differences?.length && <p>Study population differs from supplied context: {claim.patient_applicability.population_differences.map(fieldLabel).join(', ')}</p>}
        <small>{claim.patient_applicability.limitation}</small>
      </div>}
      {claim.source_matches?.map((match, i) => <div key={i}>
        <strong>{match.source_name} · {match.section.replaceAll('_', ' ')}</strong>
        <blockquote>{match.text}</blockquote>
        {safeSourceUrl(match.source_url) && <a href={safeSourceUrl(match.source_url)} target="_blank" rel="noopener noreferrer">Open source</a>}
        {match.context && match.context !== match.text && <details><summary>Surrounding source context</summary><p>{match.context}</p></details>}
      </div>)}
    </li>)}</ul>
  </details>
}
