import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test, after } from 'node:test'
import { createServer } from 'vite'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'

// Static rendering tests need neither a hot-reload port nor file watchers.
const cacheDir = await mkdtemp(join(tmpdir(), 'infermed-vite-test-'))
const server = await createServer({
  cacheDir,
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  optimizeDeps: { noDiscovery: true, include: [] },
})
after(async () => {
  await server.close()
  await rm(cacheDir, { recursive: true, force: true })
})
const { EvidenceChecks } = await server.ssrLoadModule('/src/EvidenceChecks.tsx')
test('shows supported and unresolved published claims with their source passage', () => {
  const html = renderToStaticMarkup(createElement(EvidenceChecks, { claims: [
    { text: 'Alpha can cause bleeding.', status: 'supported', reasons: ['Source matched'], evidence_ids: [], source_labels: ['Alpha label'], source_matches: [{source_name: 'Alpha label', section: 'warnings', text: 'Alpha can cause bleeding.', source_url: 'https://api.fda.gov/drug/label.json'}] },
    { text: 'Patient implication remains uncertain.', status: 'unresolved', reasons: ['Needs assessment'], evidence_ids: [] },
  ] }))
  assert.match(html, /1 source-supported/)
  assert.match(html, /Alpha can cause bleeding/)
  assert.match(html, /Patient implication remains uncertain/)
  assert.match(html, /blockquote/)
  assert.match(html, /https:\/\/api.fda.gov/)
})
test('separates structured support from missing patient applicability', () => {
  const html = renderToStaticMarkup(createElement(EvidenceChecks, {claims:[{
    text:'Alpha is a moderate inhibitor of CYP2C9.',status:'supported',verification_method:'structured_relation',claim_kind:'descriptive_claim',reasons:[],evidence_ids:[],
    patient_applicability:{status:'missing_context',required_fields:['creatinine_clearance_ml_min'],reasons:['A measured value is needed.'],limitation:'Not clinical validation.'}
  }]}))
  assert.match(html,/Structured relationship matched/)
  assert.match(html,/Patient applicability: Missing information/)
  assert.match(html,/creatinine clearance/)
})
test('does not render unsafe source links', () => {
  const html = renderToStaticMarkup(createElement(EvidenceChecks, {claims: [{text:'Claim', status:'supported',reasons:[], evidence_ids:[],source_matches:[{text:'Source',section:'warnings',source_name:'Label',source_url:'javascript:alert(1)'}]}]}))
  assert.doesNotMatch(html, /href="javascript:/)
})

test('distinguishes literal patient inputs from source support and tolerates older partial applicability', () => {
  const html = renderToStaticMarkup(createElement(EvidenceChecks, {claims: [{
    text:'Supplied age: 79 years.', status:'supported', verification_method:'literal_patient_field',
    reasons:[], evidence_ids:[], patient_applicability:{status:'not_established'}
  }]}))
  assert.match(html, /0 source-supported, 1 patient-input matched, 0 need review/)
  assert.match(html, /Supplied patient field/)
})
