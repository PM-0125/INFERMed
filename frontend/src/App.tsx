import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent, ReactNode } from 'react'
import type { Group, Vector3 } from 'three'
import './App.css'
import { analyzeInteraction, askFollowUp } from './api'
import type { AudienceMode, EvidenceBundle, EvidenceMetric, EvidenceRow, InteractionResult } from './types'

/* ─── Inline markdown renderer ────────────────────────────────────────────── */
function parseInline(text: string): ReactNode {
  const parts: ReactNode[] = []
  const re = /\*\*(.+?)\*\*|\*(.+?)\*/g
  let last = 0; let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    if (m[0].startsWith('**')) parts.push(<strong key={m.index}>{m[1]}</strong>)
    else parts.push(<em key={m.index}>{m[2]}</em>)
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts.length === 1 && typeof parts[0] === 'string' ? parts[0] : <>{parts}</>
}

function renderMarkdownBody(text: string): ReactNode {
  if (!text) return null
  const paras = text.split(/\n\n+/)
  const elements: ReactNode[] = []
  for (const [pi, para] of paras.entries()) {
    const lines = para.split('\n').map(l => l.trim()).filter(Boolean)
    if (!lines.length) continue
    const heading = lines[0].match(/^#{2,4}\s+(.+)$/)
    const contentLines = heading ? lines.slice(1) : lines
    if (heading) {
      elements.push(<h4 className="markdown-heading" key={`${pi}-heading`}>{parseInline(heading[1])}</h4>)
      if (!contentLines.length) continue
    }
    const allBullets = contentLines.length > 1 && contentLines.every(l => l.startsWith('- ') || l.startsWith('* '))
    if (allBullets) {
      elements.push(
        <ul key={pi}>
          {contentLines.map((l, li) => <li key={li}>{parseInline(l.replace(/^[-*]\s+/, ''))}</li>)}
        </ul>
      )
    } else if (contentLines.length === 1 && contentLines[0].startsWith('- ')) {
      // Single line with potential inline " - " bullet separators from LLM
      const raw = contentLines[0]
      const items = raw.split(/ - (?=\*\*|[A-Z*])/)
      if (items.length > 1) {
        elements.push(
          <ul key={pi}>
            {items.map((item, ii) => (
              <li key={ii}>{parseInline(item.replace(/^-\s+/, ''))}</li>
            ))}
          </ul>
        )
      } else {
        elements.push(<p key={pi}>{parseInline(raw.replace(/^-\s+/, ''))}</p>)
      }
    } else {
      elements.push(<p key={pi}>{parseInline(contentLines.join(' '))}</p>)
    }
  }
  return <>{elements}</>
}

type EvidenceTab = 'overview' | 'openfda' | 'internal' | 'mechanisms' | 'sources' | 'references'
type PageView = 'analyze' | 'about'

const evidenceTabs: Array<{ id: EvidenceTab; label: string }> = [
  { id: 'overview',   label: 'Overview'   },
  { id: 'openfda',    label: 'OpenFDA'    },
  { id: 'internal',   label: 'Internal'   },
  { id: 'mechanisms', label: 'Mechanisms' },
  { id: 'sources',    label: 'Sources'    },
  { id: 'references', label: 'References' },
]

const MODES: Array<{ id: AudienceMode; short: string; long: string }> = [
  { id: 'doctor',      short: 'Doctor',          long: 'Doctor - detailed PK/PD clinical analysis'          },
  { id: 'patient',     short: 'Patient',          long: 'Patient - plain-language summary'                   },
  { id: 'pv_research', short: 'Pharmacovigilance', long: 'Pharmacovigilance - statistical signal review'     },
]

const SUGGESTIONS = [
  'Elderly patient (>75 yr)?',
  'Renal impairment?',
  'Hepatic impairment?',
  'QT prolongation risk?',
  'Monitoring plan?',
  'Dose adjustment needed?',
]

/* ─── Brand Mark ─── */
const MAX_FOLLOWUPS = 3

type ThreadTurn = { role: 'user'|'assistant'; text: string; cards?: string[] }

function compactForFollowUp(text: string, maxChars: number): string {
  const compact = text.replace(/\s+/g, ' ').trim()
  return compact.length > maxChars ? `${compact.slice(0, maxChars - 1).trim()}...` : compact
}

function summarizeAssessmentForFollowUp(result: InteractionResult): string {
  const evidenceMetrics = [
    ...result.evidence.overview.metrics,
    ...result.evidence.internal.metrics,
    ...result.evidence.mechanisms.metrics,
  ]
    .map(metric => `${metric.label}: ${metric.value}`)
    .join('; ')

  return compactForFollowUp(
    [
      `Risk: ${result.risk.label}; confidence: ${result.risk.confidence}; class: ${result.risk.interactionClass}`,
      `Evidence metrics: ${evidenceMetrics || 'not available'}`,
      `Prior answer sections shown to user: ${result.assessment.map(section => section.title).join(', ')}`,
    ].join('\n'),
    1800,
  )
}

const PAPER_URL = 'https://link.springer.com/chapter/10.1007/978-3-032-23241-0_9'
const STRUCTURE_ID = '6Z4B'
const STRUCTURE_URL = `https://files.rcsb.org/download/${STRUCTURE_ID}.pdb`

const ARCHITECTURE_MERMAID = `
flowchart LR
  OpenFDA((OpenFDA)):::clinical
  PubChem((PubChem)):::chem
  BioDB((ChEMBL / UniProt / KEGG / Reactome)):::bio
  LocalDB[(Local database<br/>TWOSIDES, DILIrank<br/>Dict, DIQT)]:::local

  OpenFDA --> Retriever
  PubChem --> Retriever
  BioDB --> Retriever
  LocalDB --> Retriever

  Retriever[Retrieval + entity resolution]:::retrieval
  Context[(Normalized evidence JSON)]:::context
  Reasoner{PK/PD risk reasoning}:::reasoning
  Answer[AI explanation]:::answer
  Evidence[Evidence cards]:::evidence

  Retriever --> Context --> Reasoner
  Reasoner --> Answer
  Reasoner --> Evidence

  classDef clinical fill:#EBF4FB,stroke:#1565A8,color:#0C2D45,stroke-width:2px
  classDef chem fill:#E0F5FC,stroke:#0798C5,color:#0C2D45,stroke-width:2px
  classDef bio fill:#E6F5EE,stroke:#147A47,color:#0C2D45,stroke-width:2px
  classDef local fill:#FEF3E4,stroke:#A8520A,color:#0C2D45,stroke-width:2px
  classDef retrieval fill:#FFFFFF,stroke:#0798C5,color:#0C2D45,stroke-width:2px
  classDef context fill:#FAFCFD,stroke:#8DA5B4,color:#0C2D45,stroke-width:2px
  classDef reasoning fill:#FFFFFF,stroke:#147A47,color:#0C2D45,stroke-width:2px
  classDef answer fill:#FFFFFF,stroke:#1565A8,color:#0C2D45,stroke-width:2px
  classDef evidence fill:#FFFFFF,stroke:#A8520A,color:#0C2D45,stroke-width:2px
`

const RESEARCH_PILLARS = [
  {
    icon: '01',
    title: 'Evidence before language',
    detail: 'Retrieval, normalization, and source-specific caveats are assembled before the model writes an explanation.',
    tone: 'blue',
  },
  {
    icon: '02',
    title: 'Role-aware translation',
    detail: 'Doctor, patient, and pharmacovigilance modes are different presentations over the same interaction record.',
    tone: 'green',
  },
  {
    icon: '03',
    title: 'Inspectable provenance',
    detail: 'OpenFDA, PubChem, ChEMBL, UniProt, KEGG, Reactome, and local PK/PD sources remain visible to the user.',
    tone: 'cyan',
  },
  {
    icon: '04',
    title: 'Deployment discipline',
    detail: 'Caching, source manifests, versioned context schemas, and licensed-data boundaries are treated as product requirements.',
    tone: 'amber',
  },
]

type SourceIconKind = 'safety' | 'chemistry' | 'assay' | 'protein' | 'pathway' | 'reaction' | 'database' | 'rdf'

type DataSourceCard = {
  id: string
  name: string
  type: string
  icon: SourceIconKind
  contains: string
  contributes: string
  links: Array<{ label: string; href: string }>
}

type InternalEvidenceNote = {
  label: string
  title: string
  detail: string
  formula?: string
  interpretation: string
}

const INTERNAL_EVIDENCE_NOTES: InternalEvidenceNote[] = [
  {
    label: 'PRR',
    title: 'Proportional Reporting Ratio',
    detail: 'A disproportionality signal used with spontaneous adverse-event reports. A is the event with the selected drug or pair, C is other events with that exposure, B is the same event in the comparator background, and D is other comparator events.',
    formula: 'PRR = (A / (A + C)) / (B / (B + D))',
    interpretation: 'A higher PRR means stronger reporting disproportionality. It is a signal for review, not incidence, prevalence, or proof of causality.',
  },
  {
    label: 'DILI',
    title: 'DILIrank liver-injury concern',
    detail: 'FDA DILIrank ranks drugs by drug-induced liver injury concern using FDA-approved labeling and literature evidence. INFERMed displays the local numeric score for drug A and drug B when available.',
    interpretation: 'Use it as liver-toxicity context. A higher score indicates stronger liver-injury concern in the local reference layer, not a patient-specific probability.',
  },
  {
    label: 'DICT',
    title: 'DICTrank cardiotoxicity concern',
    detail: 'FDA DICTrank ranks human drugs by drug-induced cardiotoxicity concern using FDA-approved labeling. The local parquet converts those concern levels into a numeric score used by the evidence panel.',
    interpretation: 'Use it to flag cardiotoxicity context that may affect monitoring, caveats, or evidence interpretation.',
  },
  {
    label: 'DIQT',
    title: 'Drug-induced QT prolongation context',
    detail: 'DIQT captures drug-induced QT interval prolongation concern from the local QT safety layer. It is kept separate from broader DICTrank cardiotoxicity so QT-specific monitoring remains visible.',
    interpretation: 'Use it as a QT-prolongation warning context, especially when the answer discusses ECG monitoring, torsades risk factors, or combined QT burden.',
  },
]

const DATA_SOURCE_CARDS: DataSourceCard[] = [
  {
    id: 'openfda',
    name: 'OpenFDA / FAERS',
    type: 'Post-market safety',
    icon: 'safety',
    contains: 'Public FDA safety datasets, including FAERS adverse-event and medication-error reports collected from healthcare professionals, consumers, and manufacturers.',
    contributes: 'Adds real-world signal context for reported reactions and co-reported drugs. These signals are treated as associative, not as incidence or proof of causality.',
    links: [{ label: 'openFDA official page', href: 'https://open.fda.gov/' }],
  },
  {
    id: 'pubchem',
    name: 'PubChem + PubChemRDF',
    type: 'Chemical identity',
    icon: 'chemistry',
    contains: 'Compound identifiers, synonyms, structures, cross-references, and RDF subdomains for compounds, bioassays, genes, proteins, pathways, taxonomy, disease, and more.',
    contributes: 'Normalizes drug names to compound identity and supplies RDF-linked context for mechanism and enrichment queries.',
    links: [
      { label: 'PubChem official page', href: 'https://pubchem.ncbi.nlm.nih.gov/' },
      { label: 'PubChemRDF', href: 'https://pubchem.ncbi.nlm.nih.gov/docs/rdf' },
    ],
  },
  {
    id: 'chembl',
    name: 'ChEMBL',
    type: 'Bioactivity',
    icon: 'assay',
    contains: 'Manually curated drug-like molecules, assays, targets, activities, documents, and medicinal chemistry evidence.',
    contributes: 'Supports target and bioactivity reasoning when a potential interaction mechanism depends on binding, inhibition, or target overlap.',
    links: [{ label: 'ChEMBL official page', href: 'https://www.ebi.ac.uk/chembl/' }],
  },
  {
    id: 'uniprot',
    name: 'UniProt',
    type: 'Protein knowledge',
    icon: 'protein',
    contains: 'Protein sequences, function annotations, reviewed protein records, genes, domains, and cross-references to external biology resources.',
    contributes: 'Explains targets and enzymes at the protein level so mechanism cards can show what a gene or target actually represents.',
    links: [{ label: 'UniProt official page', href: 'https://www.uniprot.org/' }],
  },
  {
    id: 'kegg',
    name: 'KEGG',
    type: 'Pathways and enzymes',
    icon: 'pathway',
    contains: 'Pathway maps, enzyme relationships, disease, drug, genome, and chemical substance knowledge for biological systems.',
    contributes: 'Places enzymes and drug mechanisms into pathway context for PK/PD and pharmacology interpretation.',
    links: [{ label: 'KEGG official page', href: 'https://www.kegg.jp/kegg/' }],
  },
  {
    id: 'reactome',
    name: 'Reactome',
    type: 'Curated pathways',
    icon: 'reaction',
    contains: 'Curated and peer-reviewed human pathway reactions, molecular events, pathway diagrams, and analysis tools.',
    contributes: 'Adds pathway-level interpretation for target and mechanism evidence, especially when multiple proteins participate in the same biology.',
    links: [{ label: 'Reactome official page', href: 'https://reactome.org/' }],
  },
  {
    id: 'local',
    name: 'Local database',
    type: 'Curated safety layer',
    icon: 'database',
    contains: 'TWOSIDES polypharmacy side-effect relationships with PRR signals, FDA DILIrank liver injury concern labels, FDA DICTrank cardiotoxicity concern labels, and local DIQT QT-prolongation scores.',
    contributes: 'Separates side-effect disproportionality from organ-toxicity context: PRR flags reporting signals, DILIrank adds liver concern, DICTrank adds cardiotoxicity concern, and DIQT adds QT-specific context.',
    links: [
      { label: 'nSIDES / TWOSIDES', href: 'https://nsides.io/' },
      { label: 'FDA DILIrank', href: 'https://www.fda.gov/science-research/liver-toxicity-knowledge-base-ltkb/drug-induced-liver-injury-rank-dilirank-dataset' },
      { label: 'FDA DICTrank', href: 'https://www.fda.gov/science-research/bioinformatics-tools/drug-induced-cardiotoxicity-rank-dictrank-dataset' },
      { label: 'DIQTA reference', href: 'https://pubmed.ncbi.nlm.nih.gov/34718206/' },
    ],
  },
  {
    id: 'qlever',
    name: 'QLever',
    type: 'RDF acceleration',
    icon: 'rdf',
    contains: 'A fast SPARQL engine with hosted PubChem endpoints and support for large linked-data query workloads.',
    contributes: 'Acts as an optional acceleration layer for RDF retrieval; it improves performance but is not the source of biological truth by itself.',
    links: [{ label: 'QLever official page', href: 'https://qlever.dev/' }],
  },
]

const PRODUCT_USE_CASES = [
  {
    id: 'clinical',
    badge: 'Clinical review',
    title: 'Clinicians',
    detail: 'Convert interaction evidence into mechanism, monitoring, and action-oriented review.',
    points: ['Risk framing', 'Monitoring plan', 'Patient-specific caveats'],
  },
  {
    id: 'pharmacovigilance',
    badge: 'Signal review',
    title: 'Pharmacovigilance',
    detail: 'Inspect real-world signals while keeping source limitations, confounding, and provenance visible.',
    points: ['FAERS context', 'Causality caveats', 'Source traceability'],
  },
  {
    id: 'research',
    badge: 'Mechanism discovery',
    title: 'Researchers',
    detail: 'Connect compounds, targets, enzymes, pathways, and PK/PD hypotheses into a reviewable evidence record.',
    points: ['Target enrichment', 'Pathway context', 'Hypothesis support'],
  },
]

const SOURCE_ICON_GLYPHS: Record<SourceIconKind, ReactNode> = {
  safety: (
    <>
      <path className="icon-fill" d="M32 6 52 14v15c0 13-8.5 23.5-20 29C20.5 52.5 12 42 12 29V14L32 6Z" />
      <path className="icon-accent" d="M29 19h6v10h10v6H35v10h-6V35H19v-6h10V19Z" />
    </>
  ),
  chemistry: (
    <>
      <path className="icon-line" d="M22 24 32 15l12 8M22 24l5 17m17-18-5 18M27 41h12" />
      <circle className="icon-fill" cx="32" cy="15" r="7" />
      <circle className="icon-accent" cx="22" cy="24" r="6" />
      <circle className="icon-accent" cx="44" cy="23" r="6" />
      <circle className="icon-fill" cx="27" cy="41" r="6" />
      <circle className="icon-fill" cx="39" cy="41" r="6" />
    </>
  ),
  assay: (
    <>
      <rect className="icon-fill" x="13" y="18" width="38" height="30" rx="8" />
      {[21, 32, 43].map(x => (
        <g key={x}>
          <circle className="icon-accent" cx={x} cy="28" r="4" />
          <circle className="icon-accent" cx={x} cy="39" r="4" />
        </g>
      ))}
    </>
  ),
  protein: (
    <>
      <path className="icon-line thick" d="M15 39c8-23 27 15 34-8" />
      <path className="icon-line thick" d="M17 25c11-13 23 18 34 3" />
      <circle className="icon-accent" cx="24" cy="38" r="5" />
      <circle className="icon-fill" cx="41" cy="27" r="5" />
    </>
  ),
  pathway: (
    <>
      <path className="icon-line" d="M16 44h12l9-12h12M16 20h14l7 12" />
      <circle className="icon-fill" cx="16" cy="20" r="6" />
      <circle className="icon-accent" cx="37" cy="32" r="7" />
      <circle className="icon-fill" cx="49" cy="32" r="6" />
      <circle className="icon-accent" cx="16" cy="44" r="6" />
    </>
  ),
  reaction: (
    <>
      <path className="icon-line thick" d="M20 24a16 16 0 0 1 26-5" />
      <path className="icon-line thick" d="M44 40a16 16 0 0 1-26 5" />
      <path className="icon-accent" d="M47 16v12H35l4-4-3-6 7 2 4-4Z" />
      <path className="icon-fill" d="M17 48V36h12l-4 4 3 6-7-2-4 4Z" />
      <circle className="icon-accent" cx="32" cy="32" r="6" />
    </>
  ),
  database: (
    <>
      <ellipse className="icon-accent" cx="32" cy="17" rx="17" ry="8" />
      <path className="icon-fill" d="M15 17v27c0 4.5 7.6 8 17 8s17-3.5 17-8V17c0 4.5-7.6 8-17 8s-17-3.5-17-8Z" />
      <path className="icon-line" d="M15 29c0 4.5 7.6 8 17 8s17-3.5 17-8M15 40c0 4.5 7.6 8 17 8s17-3.5 17-8" />
    </>
  ),
  rdf: (
    <>
      <path className="icon-line" d="M19 22h26v20H19zM19 22l8-8h26v20l-8 8M45 22l8-8M45 42V22" />
      <circle className="icon-accent" cx="22" cy="25" r="4" />
      <circle className="icon-fill" cx="36" cy="32" r="5" />
      <circle className="icon-accent" cx="47" cy="17" r="4" />
      <path className="icon-line" d="M22 25 36 32l11-15" />
    </>
  ),
}

function SourceIcon({ kind }: { kind: SourceIconKind }) {
  return (
    <div className={`source-icon-3d ${kind}`} aria-hidden="true">
      <svg viewBox="0 0 64 64" role="img" focusable="false">
        {SOURCE_ICON_GLYPHS[kind]}
      </svg>
    </div>
  )
}

function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 20 20" fill="none">
        <circle cx="10"   cy="5"  r="2.5" fill="#7DD4ED"/>
        <circle cx="4.5"  cy="15" r="2.5" fill="#6DCDA3"/>
        <circle cx="15.5" cy="15" r="2.5" fill="#90C8E8"/>
        <line x1="10" y1="7.4"  x2="5.3"  y2="12.6" stroke="rgba(255,255,255,.55)" strokeWidth="1.2" strokeLinecap="round"/>
        <line x1="10" y1="7.4"  x2="14.7" y2="12.6" stroke="rgba(255,255,255,.55)" strokeWidth="1.2" strokeLinecap="round"/>
        <line x1="7.1" y1="15"  x2="12.9" y2="15"   stroke="rgba(255,255,255,.55)" strokeWidth="1.2" strokeLinecap="round"/>
      </svg>
    </div>
  )
}

/* ─── Skeleton Loader ─── */
function InfermedRelayBackdrop() {
  return (
    <div className="workspace-relay-backdrop" aria-hidden="true">
      <div className="relay-brand">
        <svg className="relay-network" viewBox="0 0 420 420" role="img" focusable="false">
          <defs>
            <linearGradient id="relay-ray-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#7DD4ED" stopOpacity="0" />
              <stop offset="38%" stopColor="#7DD4ED" stopOpacity="0.95" />
              <stop offset="68%" stopColor="#6DCDA3" stopOpacity="0.95" />
              <stop offset="100%" stopColor="#90C8E8" stopOpacity="0" />
            </linearGradient>
            <filter id="relay-glow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="7" result="blur" />
              <feColorMatrix
                in="blur"
                result="glow"
                type="matrix"
                values="0 0 0 0 0.23 0 0 0 0 0.78 0 0 0 0 0.95 0 0 0 .75 0"
              />
              <feMerge>
                <feMergeNode in="glow" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          <path className="relay-halo" d="M210 46C302 46 374 118 374 210S302 374 210 374 46 302 46 210 118 46 210 46Z" />
          <path className="relay-edge" d="M210 70 92 316 328 316Z" />
          <path className="relay-edge edge-secondary" d="M210 70 210 214M92 316 210 214M328 316 210 214" />

          <path className="relay-ray ray-a" d="M210 70 92 316 328 316 210 70" />
          <path className="relay-ray ray-b" d="M210 70 210 214 92 316 210 70" />
          <path className="relay-ray ray-c" d="M328 316 210 214 92 316 328 316" />

          <g className="relay-node node-top" filter="url(#relay-glow)">
            <circle cx="210" cy="70" r="18" />
            <circle cx="210" cy="70" r="6" />
          </g>
          <g className="relay-node node-left" filter="url(#relay-glow)">
            <circle cx="92" cy="316" r="18" />
            <circle cx="92" cy="316" r="6" />
          </g>
          <g className="relay-node node-right" filter="url(#relay-glow)">
            <circle cx="328" cy="316" r="18" />
            <circle cx="328" cy="316" r="6" />
          </g>
          <g className="relay-node node-center" filter="url(#relay-glow)">
            <circle cx="210" cy="214" r="14" />
            <circle cx="210" cy="214" r="5" />
          </g>
        </svg>

        <div className="relay-wordmark">
          <strong>INFERMed</strong>
          <span>Intelligent Navigator for Evidence-based Retrieval in Medicine</span>
        </div>
      </div>
    </div>
  )
}

function SkeletonLoader() {
  return (
    <div className="workspace-grid">
      <div className="answer-panel">
        <div className="skeleton-block" style={{ height: 60,  marginBottom: 14 }} />
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:14 }}>
          <div className="skeleton-block" style={{ height: 78 }} />
          <div className="skeleton-block" style={{ height: 78 }} />
        </div>
        {[100,85,72,90,68].map(w => (
          <div key={w} className="skeleton-block" style={{ height:15, width:`${w}%`, marginBottom:10 }} />
        ))}
        <div className="skeleton-block" style={{ height:15, width:'55%' }} />
      </div>
      <div className="evidence-panel">
        <div className="skeleton-block" style={{ height:40, marginBottom:12 }} />
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8, marginBottom:12 }}>
          {[1,2,3].map(i => <div key={i} className="skeleton-block" style={{ height:60 }} />)}
        </div>
        {[90,75,60,80].map(w => (
          <div key={w} className="skeleton-block" style={{ height:14, width:`${w}%`, marginBottom:10 }} />
        ))}
      </div>
    </div>
  )
}

/* ─── Source activity indicator ─── */
function SourceDot() {
  return <span className="source-live-dot" aria-hidden="true" />
}

function ChemicalStructure({ cid, name }: { cid?: string; name: string }) {
  const [failed, setFailed] = useState(false)

  if (!cid || failed) {
    return (
      <div className="structure-wrap">
        <span className="structure-placeholder">Structure unavailable</span>
      </div>
    )
  }

  return (
    <div className="structure-wrap">
      <img
        src={`https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/${encodeURIComponent(cid)}/PNG?record_type=2d&image_size=640x300`}
        alt={`${name} 2D structure`}
        className="structure-img"
        loading="lazy"
        onError={() => setFailed(true)}
      />
    </div>
  )
}

/* ─────────────────────────────────────────────
   MAIN APP
───────────────────────────────────────────── */
interface PdbAtom {
  atom: string
  element: string
  record: string
  resName: string
  chain: string
  resSeq: number
  point: Vector3
}

const SOLVENT_OR_ION = new Set(['HOH', 'WAT', 'DOD', 'SO4', 'GOL', 'EDO', 'DMS', 'CL', 'NA', 'K', 'MG', 'ZN', 'CA'])
type ThreeModule = typeof import('three')

function parsePdbAtoms(three: ThreeModule, pdb: string): PdbAtom[] {
  return pdb.split('\n').flatMap(line => {
    const record = line.slice(0, 6).trim()
    if (record !== 'ATOM' && record !== 'HETATM') return []
    const x = Number.parseFloat(line.slice(30, 38))
    const y = Number.parseFloat(line.slice(38, 46))
    const z = Number.parseFloat(line.slice(46, 54))
    if (![x, y, z].every(Number.isFinite)) return []
    const atom = line.slice(12, 16).trim()
    const inferred = atom.replace(/[^A-Za-z]/g, '').slice(0, 1).toUpperCase()
    return [{
      atom,
      element: line.slice(76, 78).trim() || inferred || 'C',
      record,
      resName: line.slice(17, 20).trim(),
      chain: line.slice(21, 22).trim(),
      resSeq: Number.parseInt(line.slice(22, 26).trim(), 10),
      point: new three.Vector3(x, y, z),
    }]
  })
}

function atomColor(element: string) {
  const e = element.toUpperCase()
  if (e === 'O') return 0xE64B45
  if (e === 'N') return 0x3A78D4
  if (e === 'S') return 0xE0A72F
  if (e === 'P') return 0xD47A1F
  if (e === 'F' || e === 'CL' || e === 'BR') return 0x32A852
  return 0x2C4255
}

function normalizeAtoms(three: ThreeModule, atoms: PdbAtom[]) {
  const box = new three.Box3().setFromPoints(atoms.map(a => a.point))
  const center = box.getCenter(new three.Vector3())
  const size = box.getSize(new three.Vector3()).length() || 1
  const scale = 8.2 / size
  return atoms.map(atom => ({
    ...atom,
    point: atom.point.clone().sub(center).multiplyScalar(scale),
  }))
}

function makeSphere(three: ThreeModule, color: number, radius: number) {
  return new three.Mesh(
    new three.SphereGeometry(radius, 18, 14),
    new three.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.08 })
  )
}

function makeBond(three: ThreeModule, a: Vector3, b: Vector3, color = 0x8DA5B4, radius = 0.035) {
  const diff = b.clone().sub(a)
  const mesh = new three.Mesh(
    new three.CylinderGeometry(radius, radius, diff.length(), 10),
    new three.MeshStandardMaterial({ color, roughness: 0.7 })
  )
  mesh.position.copy(a).add(b).multiplyScalar(0.5)
  mesh.quaternion.setFromUnitVectors(new three.Vector3(0, 1, 0), diff.normalize())
  return mesh
}

function addProteinStructure(three: ThreeModule, group: Group, atoms: PdbAtom[]) {
  const normalized = normalizeAtoms(three, atoms)
  const ca = normalized.filter(atom => atom.record === 'ATOM' && atom.atom === 'CA')
  if (ca.length > 8) {
    const stride = Math.max(1, Math.floor(ca.length / 260))
    const points = ca.filter((_, index) => index % stride === 0).map(atom => atom.point)
    const curve = new three.CatmullRomCurve3(points, false, 'centripetal', 0.45)
    const tube = new three.Mesh(
      new three.TubeGeometry(curve, Math.min(points.length * 4, 700), 0.055, 8, false),
      new three.MeshStandardMaterial({ color: 0x0798C5, roughness: 0.42, metalness: 0.12 })
    )
    group.add(tube)
  }

  const residue790 = normalized.filter(atom => atom.record === 'ATOM' && atom.resSeq === 790)
  residue790.forEach(atom => {
    const sphere = makeSphere(three, 0xD58A19, atom.atom === 'CA' ? 0.18 : 0.12)
    sphere.position.copy(atom.point)
    group.add(sphere)
  })

  const ligandGroups = new Map<string, PdbAtom[]>()
  normalized
    .filter(atom => atom.record === 'HETATM' && !SOLVENT_OR_ION.has(atom.resName))
    .forEach(atom => {
      const key = `${atom.resName}:${atom.chain}:${atom.resSeq}`
      ligandGroups.set(key, [...(ligandGroups.get(key) ?? []), atom])
    })

  Array.from(ligandGroups.values())
    .filter(ligand => ligand.length >= 8)
    .sort((a, b) => b.length - a.length)
    .slice(0, 2)
    .forEach((ligand, ligandIndex) => {
      ligand.forEach(atom => {
        const sphere = makeSphere(three, atomColor(atom.element), ligandIndex === 0 ? 0.13 : 0.105)
        sphere.position.copy(atom.point)
        group.add(sphere)
      })
      for (let i = 0; i < ligand.length; i += 1) {
        for (let j = i + 1; j < ligand.length; j += 1) {
          const distance = ligand[i].point.distanceTo(ligand[j].point)
          if (distance > 0.12 && distance < 0.34) {
            group.add(makeBond(three, ligand[i].point, ligand[j].point, ligandIndex === 0 ? 0x147A47 : 0x1565A8, 0.018))
          }
        }
      }
    })
}

function addFallbackStructure(three: ThreeModule, group: Group) {
  const points = Array.from({ length: 130 }, (_, index) => {
    const t = index / 12
    return new three.Vector3(
      Math.sin(t) * 2.1 + Math.sin(t * 0.31) * 0.9,
      (index - 65) * 0.045,
      Math.cos(t * 0.86) * 1.45
    )
  })
  group.add(new three.Mesh(
    new three.TubeGeometry(new three.CatmullRomCurve3(points), 420, 0.07, 10, false),
    new three.MeshStandardMaterial({ color: 0x0798C5, roughness: 0.45, metalness: 0.1 })
  ))
  const ligand = [
    new three.Vector3(-.45, -.15, .65),
    new three.Vector3(.05, -.03, .42),
    new three.Vector3(.55, .05, .66),
    new three.Vector3(.88, .22, .28),
    new three.Vector3(.35, .38, -.04),
    new three.Vector3(-.22, .24, .08),
  ]
  ligand.forEach((point, index) => {
    const sphere = makeSphere(three, index % 3 === 0 ? 0xE64B45 : index % 3 === 1 ? 0x3A78D4 : 0x2C4255, 0.16)
    sphere.position.copy(point)
    group.add(sphere)
    if (index > 0) group.add(makeBond(three, ligand[index - 1], point, 0x147A47, 0.028))
  })
  const mutation = makeSphere(three, 0xD58A19, 0.26)
  mutation.position.set(-.88, .12, .52)
  group.add(mutation)
}

function DrugProteinMutationModel() {
  const mountRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return

    let active = true
    let cleanup: (() => void) | null = null

    void import('three').then(three => {
      if (!active) return

      const scene = new three.Scene()
      const camera = new three.PerspectiveCamera(35, 1, 0.1, 100)
      camera.position.set(0, 0, 10)
      const renderer = new three.WebGLRenderer({ antialias: true, alpha: true })
      renderer.setClearColor(0x000000, 0)
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
      mount.appendChild(renderer.domElement)

      const group = new three.Group()
      group.rotation.set(-0.45, 0.45, 0.08)
      scene.add(group)
      scene.add(new three.AmbientLight(0xffffff, 1.9))
      const keyLight = new three.DirectionalLight(0xffffff, 2.8)
      keyLight.position.set(4, 5, 7)
      scene.add(keyLight)
      const rimLight = new three.DirectionalLight(0x7dd4ed, 2.2)
      rimLight.position.set(-5, -2, 5)
      scene.add(rimLight)

      const populate = async () => {
        try {
          const response = await fetch(STRUCTURE_URL)
          if (!response.ok) throw new Error('structure unavailable')
          const pdb = await response.text()
          if (!active) return
          addProteinStructure(three, group, parsePdbAtoms(three, pdb))
        } catch {
          if (active) addFallbackStructure(three, group)
        }
      }
      void populate()

      const resize = () => {
        const rect = mount.getBoundingClientRect()
        const width = Math.max(rect.width, 260)
        const height = Math.max(rect.height, 300)
        renderer.setSize(width, height, false)
        camera.aspect = width / height
        camera.updateProjectionMatrix()
      }
      resize()
      const observer = new ResizeObserver(resize)
      observer.observe(mount)

      let frame = 0
      const animate = () => {
        if (!active) return
        frame = requestAnimationFrame(animate)
        group.rotation.y += 0.004
        group.rotation.x = -0.45 + Math.sin(Date.now() / 2200) * 0.07
        renderer.render(scene, camera)
      }
      animate()

      cleanup = () => {
        cancelAnimationFrame(frame)
        observer.disconnect()
        renderer.dispose()
        mount.removeChild(renderer.domElement)
      }
    })

    return () => {
      active = false
      cleanup?.()
    }
  }, [])

  return <div ref={mountRef} className="bio-structure-stage" aria-label={`RCSB ${STRUCTURE_ID} EGFR T790M drug-protein structure`} />
}

function MermaidArchitecture({ chart }: { chart: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let active = true
    const renderId = `infermed-architecture-${Math.random().toString(36).slice(2)}`

    void import('mermaid').then(({ default: mermaid }) => {
      if (!active) return
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        themeVariables: {
          fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
          primaryColor: '#EBF4FB',
          primaryTextColor: '#0C2D45',
          primaryBorderColor: '#1565A8',
          lineColor: '#7997AA',
          secondaryColor: '#E0F5FC',
          tertiaryColor: '#E6F5EE',
          background: 'transparent',
          mainBkg: '#FFFFFF',
          nodeBorder: '#C5D4DE',
          clusterBkg: '#FAFCFD',
          clusterBorder: '#DDE8EE',
        },
        flowchart: {
          curve: 'basis',
          htmlLabels: true,
          nodeSpacing: 42,
          rankSpacing: 54,
        },
      })
      return mermaid.render(renderId, chart)
    }).then(result => {
      if (!active || !result || !container) return
      container.innerHTML = result.svg
      result.bindFunctions?.(container)
    }).catch(() => {
      if (active) setFailed(true)
    })

    return () => {
      active = false
      if (container) container.innerHTML = ''
    }
  }, [chart])

  if (failed) {
    return (
      <div className="mermaid-fallback" role="img" aria-label="System architecture diagram unavailable">
        System architecture diagram unavailable.
      </div>
    )
  }

  return <div ref={containerRef} className="mermaid-diagram" aria-label="Rendered INFERMed system architecture diagram" />
}

function AboutPage({ onAnalyze }: { onAnalyze: () => void }) {
  return (
    <main className="about-page" aria-label="About INFERMed">
      <section className="research-hero">
        <div className="research-hero-copy">
          <div className="research-hero-body">
            <div>
              <p className="section-eyebrow">Research translation</p>
              <h1>Evidence-first drug interaction intelligence for clinical and translational teams.</h1>
              <p>
                INFERMed began as published thesis research and is now being shaped into a product-grade
                platform: retrieve source evidence, normalize it into an auditable interaction record,
                and use AI to explain what the evidence supports, where it is uncertain, and what should be
                monitored next.
              </p>
              <div className="research-actions">
                <button className="btn-analyze" type="button" onClick={onAnalyze}>Start an analysis</button>
              </div>
            </div>
            <DrugProteinMutationModel />
          </div>
        </div>

        <aside className="research-summary-card" aria-label="Research summary">
          <span className="section-eyebrow">Published foundation</span>
          <h2>INFERMed: A PK/PD-aware retrieval-augmented system for explainable DDI analysis.</h2>
          <p>
            The Springer Nature chapter presents INFERMed as a multi-source retrieval system that
            combines PubChemRDF through QLever, DuckDB-backed clinical and risk tables, OpenFDA
            adverse-event reports, and a PK/PD reasoning layer to explain interaction mechanisms.
            Its evaluation on known drug pairs showed stronger results for enzyme-mediated inhibition
            and induction than for sparse supplement, absorption, or harder non-enzyme edge cases.
          </p>
          <a className="paper-link" href={PAPER_URL} target="_blank" rel="noopener noreferrer">
            Read the Springer Nature chapter
          </a>
        </aside>
      </section>

      <section className="research-section">
        <div className="research-section-head">
          <div>
            <p className="section-eyebrow">Research principles</p>
            <h2>Built around answer quality, provenance, and reviewability.</h2>
          </div>
          <p>
            Every answer should be explainable from retrieved evidence, source status, and mechanistic context.
          </p>
        </div>
        <div className="research-pillar-grid">
          {RESEARCH_PILLARS.map(item => (
            <article className={`research-pillar ${item.tone}`} key={item.title}>
              <span>{item.icon}</span>
              <strong>{item.title}</strong>
              <p>{item.detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="research-panel architecture-panel">
        <div className="platform-panel-head">
          <span className="section-eyebrow">System architecture</span>
          <h3>Designed for medical review, not black-box output</h3>
        </div>
        <MermaidArchitecture chart={ARCHITECTURE_MERMAID} />
      </section>

      <section className="research-panel data-source-panel">
        <div className="platform-panel-head data-source-head">
          <div>
            <span className="section-eyebrow">Data source system</span>
            <h3>What each evidence source contributes</h3>
          </div>
          <p>
            INFERMed separates signal, chemistry, bioactivity, protein, pathway, and local safety layers so the AI answer can cite inspectable evidence instead of blending every source together.
          </p>
        </div>
        <div className="source-explainer-grid" aria-label="Data source contribution cards">
          {DATA_SOURCE_CARDS.map(source => (
            <article className={`source-explainer-card ${source.id}`} key={source.id}>
              <div className="source-explainer-top">
                <div>
                  <span className="source-type">{source.type}</span>
                  <h4>{source.name}</h4>
                </div>
                <SourceIcon kind={source.icon} />
              </div>
              <div className="source-explainer-body">
                <div className="source-fact">
                  <strong>What it contains</strong>
                  <p>{source.contains}</p>
                </div>
                <div className="source-fact">
                  <strong>How INFERMed uses it</strong>
                  <p>{source.contributes}</p>
                </div>
              </div>
              <div className="source-links" aria-label={`${source.name} official links`}>
                {source.links.map(link => (
                  <a key={link.href} href={link.href} target="_blank" rel="noopener noreferrer">
                    {link.label}
                  </a>
                ))}
              </div>
            </article>
          ))}
        </div>
        <div className="local-method-panel" aria-label="Local safety metric interpretation">
          <div className="local-method-head">
            <span className="section-eyebrow">Local safety metrics</span>
            <h4>How the internal scores should be read</h4>
          </div>
          <div className="local-method-grid">
            {INTERNAL_EVIDENCE_NOTES.map(note => (
              <article className="local-method-card" key={note.label}>
                <span>{note.label}</span>
                <strong>{note.title}</strong>
                {note.formula && <code>{note.formula}</code>}
                <p>{note.interpretation}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="research-section research-use product-use-panel">
        <div className="product-use-copy">
          <p className="section-eyebrow">Product use cases</p>
          <h2>Built for teams who need drug-interaction answers they can act on and audit.</h2>
          <p>
            INFERMed is not only a lookup screen. It is a review workspace where AI explanation,
            normalized evidence, and source provenance stay together.
          </p>
        </div>
        <div className="research-use-grid">
          {PRODUCT_USE_CASES.map(useCase => (
            <article className={`product-use-card ${useCase.id}`} key={useCase.id}>
              <div className="use-card-top">
                <span className="use-case-icon" aria-hidden="true"><span /></span>
                <span className="use-case-badge">{useCase.badge}</span>
              </div>
              <strong>{useCase.title}</strong>
              <span>{useCase.detail}</span>
              <ul>
                {useCase.points.map(point => <li key={point}>{point}</li>)}
              </ul>
            </article>
          ))}
        </div>
      </section>
    </main>
  )
}

export default function App() {
  const [drugs, setDrugs]       = useState<string[]>([])
  const [draft, setDraft]       = useState('')
  const [mode, setMode]         = useState<AudienceMode>('doctor')
  const [doRefresh, setRefresh] = useState(false)
  const [activeTab, setTab]     = useState<EvidenceTab>('overview')
  const [result, setResult]     = useState<InteractionResult | null>(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const [followUp, setFollowUp] = useState('')
  const [thread, setThread]     = useState<ThreadTurn[]>([])
  const [darkMode, setDarkMode] = useState(false)
  const [page, setPage]         = useState<PageView>('analyze')

  function toggleDark() {
    setDarkMode(d => {
      document.documentElement.setAttribute('data-theme', d ? '' : 'dark')
      return !d
    })
  }

  const canAnalyze = drugs.length >= 2 && !loading
  const pairLabel  = useMemo(() => drugs.slice(0, 2).join(' + '), [drugs])
  const currentMode = MODES.find(m => m.id === mode)!
  const followUpCount = thread.filter(item => item.role === 'user').length
  const followUpsRemaining = Math.max(0, MAX_FOLLOWUPS - followUpCount)
  const canAskFollowUp = Boolean(result && followUp.trim() && followUpsRemaining > 0)

  function openPage(nextPage: PageView) {
    setPage(nextPage)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  /* ── drug chip helpers ── */
  function addDrug(v: string) {
    const s = v.trim().replace(/\s+/g,' ')
    if (!s) return
    setDrugs(arr => arr.some(d => d.toLowerCase()===s.toLowerCase()) ? arr : [...arr, s])
    setDraft('')
  }
  function removeDrug(i: number) { setDrugs(arr => arr.filter((_,j)=>j!==i)) }
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key==='Enter'||e.key===',') { e.preventDefault(); addDrug(draft) }
    if (e.key==='Backspace' && !draft && drugs.length) removeDrug(drugs.length-1)
  }
  function clearAll() { setDrugs([]); setResult(null); setThread([]); setError('') }

  /* ── analyze ── */
  async function analyze() {
    if (!canAnalyze) return
    setLoading(true); setError(''); setThread([])
    try {
      const r = await analyzeInteraction({ drugs: drugs.slice(0,2), mode, refreshEvidence: doRefresh })
      setResult(r); setTab('overview')
    } catch(e) {
      setError(e instanceof Error ? e.message : 'Analysis failed.')
    } finally { setLoading(false) }
  }

  /* ── follow-up ── */
  async function sendFollowUp(e: FormEvent) {
    e.preventDefault()
    const q = followUp.trim(); if (!q||!result) return
    const currentFollowUpCount = thread.filter(item => item.role === 'user').length
    if (currentFollowUpCount >= MAX_FOLLOWUPS) {
      setThread(t => [
        ...t,
        {
          role: 'assistant',
          text: 'Follow-up limit reached for this interaction record. Start a new analysis to continue with a clean evidence context.',
          cards: ['Runtime'],
        },
      ])
      return
    }
    const requestHistory = thread
      .slice(-6)
      .map(({ role, text }) => ({ role, text: compactForFollowUp(text, 1400) }))
    setFollowUp('')
    setThread(t => [...t, { role:'user', text:q }])
    try {
      const r = await askFollowUp({
        question: q,
        drugs: drugs.slice(0,2),
        mode,
        history: requestHistory,
        priorAssessment: summarizeAssessmentForFollowUp(result),
        followUpCount: currentFollowUpCount,
      })
      setThread(t => [...t, { role:'assistant', text:r.answer, cards:r.citedCards }])
    } catch(e) {
      setThread(t => [...t, { role:'assistant', text: e instanceof Error ? e.message : 'Failed.', cards:['Runtime'] }])
    }
  }

  /* ─────────────── RENDER ─────────────── */
  return (
    <div className="app-shell">

      {/* ══════════ TOP BAR ══════════ */}
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <div>
            <span className="brand-name">INFERMed</span>
            <span className="brand-tagline">Intelligent Navigator for Evidence-based Retrieval in Medicine</span>
          </div>
        </div>

        <nav className="topbar-nav" aria-label="Primary navigation">
          <button
            type="button"
            className={page === 'analyze' ? 'active' : ''}
            onClick={() => openPage('analyze')}
          >
            Analyze
          </button>
          <button
            type="button"
            className={page === 'about' ? 'active' : ''}
            onClick={() => openPage('about')}
          >
            About
          </button>
        </nav>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, position: 'relative', zIndex: 1 }}>
          <div className="topbar-status">
            <SourceDot />
            <span>{page === 'analyze' ? `Live - ${currentMode.short}` : 'Research platform'}</span>
          </div>
          <button
            className="theme-toggle"
            onClick={toggleDark}
            title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
            aria-label="Toggle dark mode"
          >
            {darkMode ? 'Light' : 'Dark'}
          </button>
        </div>
      </header>

      {/* ══════════ SEARCH HERO (edge-to-edge) ══════════ */}
      {page === 'analyze' ? (
        <>
      <section className="search-hero" id="search" aria-label="Drug search">
        <div className="search-hero-inner">

          {/* One-line search bar with inline mode selector */}
          <div className="search-bar-wrap">
            <div className="search-input-group">
              <select
                className="mode-select"
                value={mode}
                onChange={e => setMode(e.target.value as AudienceMode)}
                aria-label="Audience mode"
                title={currentMode.long}
              >
                {MODES.map(m => (
                  <option key={m.id} value={m.id}>{m.short}</option>
                ))}
              </select>
              <div
                className="drug-token-field has-mode-select"
                onClick={() => document.getElementById('drug-input')?.focus()}
                role="group"
                aria-label="Drug search tokens"
              >
                {drugs.map((d,i) => (
                  <button
                    key={`${d}-${i}`}
                    className="drug-token"
                    type="button"
                    onClick={e => { e.stopPropagation(); removeDrug(i) }}
                    aria-label={`Remove ${d}`}
                  >
                    {d}<span aria-hidden="true">x</span>
                  </button>
                ))}
                <input
                  id="drug-input"
                  value={draft}
                  onChange={e=>setDraft(e.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder={drugs.length ? 'Add another medicine...' : 'Enter drug name and press Enter...'}
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
            </div>

            <div className="search-actions">
              {error && <span className="search-error" role="alert" title={error}>!</span>}
              <label className="refresh-toggle" title="Bypass cache and re-fetch all evidence sources">
                <input type="checkbox" checked={doRefresh} onChange={e=>setRefresh(e.target.checked)} />
                <span>Refresh source knowledge</span>
              </label>
              <button className="btn-clear" type="button" onClick={clearAll} disabled={!drugs.length&&!result}>
                Clear
              </button>
              <button className="btn-analyze" type="button" onClick={analyze} disabled={!canAnalyze}>
                {loading
                  ? <><span className="spinner" aria-hidden="true"/>Analyzing...</>
                  : <>Analyze interaction</>}
              </button>
            </div>
          </div>

          {drugs.length > 2 && (
            <p className="n-drug-note" role="note">
              Analysis uses first two drugs. Additional chips retained for N-drug design.
            </p>
          )}
        </div>
      </section>

      {/* ══════════ WORKSPACE ══════════ */}
      <div className={`workspace-wrap ${result ? 'has-result' : loading ? 'is-loading' : 'is-empty'}`}>
        <InfermedRelayBackdrop />
        {loading ? (
          <SkeletonLoader />
        ) : result ? (
          <div className="workspace-grid">

            {/* ── LEFT: AI Assessment ── */}
            <article className="answer-panel" id="answer">

              {/* Risk header bar */}
              <div className={`risk-header risk-${result.risk.level}`}>
                <div className="risk-header-left">
                  <span className="risk-badge">{result.risk.label}</span>
                  <div>
                    <div className="risk-pair">{pairLabel}</div>
                    <div className="risk-class">{result.risk.interactionClass ?? 'Drug-Drug Interaction'}</div>
                  </div>
                </div>
              </div>

              {/* Drug identity row */}
              <div className="identity-row">
                {result.drugs.map(drug => (
                  <div className="identity-cell" key={drug.name}>
                    <span className="identity-kind">Compound</span>
                    <strong className="identity-name">{drug.name}</strong>
                    <ChemicalStructure cid={drug.pubchemCid} name={drug.name} />
                    <div className="identity-ids">
                      {drug.pubchemCid && (
                        <a href={`https://pubchem.ncbi.nlm.nih.gov/compound/${drug.pubchemCid}`} target="_blank" rel="noopener noreferrer">
                          PubChem {drug.pubchemCid}
                        </a>
                      )}
                      {drug.drugbankId && (
                        <a href={`https://go.drugbank.com/drugs/${drug.drugbankId}`} target="_blank" rel="noopener noreferrer">
                          DrugBank {drug.drugbankId}
                        </a>
                      )}
                      {!drug.pubchemCid && !drug.drugbankId && <span className="no-id">No IDs resolved</span>}
                    </div>
                  </div>
                ))}
              </div>

              {/* Narrative sections */}
              <div className="narrative">
                {result.assessment.map((s, i) => (
                  <div className="narrative-section" key={s.title} style={{ animationDelay: `${i*60}ms` }}>
                    <h3>{s.title}</h3>
                    <div className="narrative-body">{renderMarkdownBody(s.body)}</div>
                  </div>
                ))}
              </div>

              {/* Follow-up */}
              <div className="followup-zone">
                <div className="followup-header">
                  <span className="section-eyebrow">Scenario follow-up</span>
                  <span className="followup-meta">
                    Scoped to this interaction record - {followUpsRemaining} follow-up{followUpsRemaining === 1 ? '' : 's'} left
                  </span>
                </div>
                <div className="suggestion-chips" aria-label="Suggested questions">
                  {SUGGESTIONS.map(s => (
                    <button
                      key={s}
                      className="suggestion-chip"
                      type="button"
                      onClick={() => setFollowUp(s)}
                      disabled={followUpsRemaining === 0}
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <form className="followup-form" onSubmit={sendFollowUp}>
                  <input
                    className="followup-input"
                    value={followUp}
                    onChange={e => setFollowUp(e.target.value)}
                    placeholder={followUpsRemaining > 0 ? 'Ask a scenario-specific question...' : 'Start a new analysis to ask more follow-ups'}
                    autoComplete="off"
                    disabled={followUpsRemaining === 0}
                  />
                  <button className="followup-send" type="submit" disabled={!canAskFollowUp}>Ask</button>
                </form>
                {thread.length > 0 && (
                  <div className="thread" aria-live="polite">
                    {thread.map((item, i) => (
                      <div className={`thread-msg ${item.role}`} key={i}>
                        <strong>{item.role==='user' ? 'You' : 'INFERMed AI'}</strong>
                        <div className="narrative-body">{renderMarkdownBody(item.text)}</div>
                        {item.cards && (
                          <div className="thread-cards">
                            {item.cards.map(c => <span key={c}>{c}</span>)}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </article>

            {/* ── RIGHT: Evidence Panel ── */}
            <aside className="evidence-panel" id="evidence" aria-label="Evidence cards">
              <div className="evidence-header">
                <span className="section-eyebrow">Evidence basis</span>
                <h2 className="evidence-title">Why this answer?</h2>
              </div>
              <div className="tab-strip" role="tablist">
                {evidenceTabs.map(t => (
                  <button
                    key={t.id}
                    role="tab"
                    aria-selected={activeTab===t.id}
                    className={activeTab===t.id ? 'active' : ''}
                    onClick={() => setTab(t.id)}
                    type="button"
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <div className="tab-content" role="tabpanel" key={activeTab}>
                <EvidenceCard
                  evidence={result.evidence}
                  activeTab={activeTab}
                  drugNames={[result.drugs[0]?.name ?? 'Drug A', result.drugs[1]?.name ?? 'Drug B']}
                />
              </div>
            </aside>
          </div>

        ) : (
          /* ── Empty state ── */
          <div className="empty-shell" aria-label="Ready for analysis">
            <div className="empty-hero">
              <p className="empty-lead">
                Enter two medicines above to generate an AI assessment backed by OpenFDA FAERS signals, pharmacokinetic enzyme data, and curated PK/PD mechanisms.
              </p>
            </div>
          </div>
        )}

        {/* ── Disclaimer ── */}
        <div className="disclaimer" role="note">
          <strong>Research Use Only.</strong>
          {' '}This tool is for informational purposes and expert assistance - always consult licensed clinicians. AI can make mistakes.
        </div>
      </div>
        </>
      ) : (
        <AboutPage onAnalyze={() => openPage('analyze')} />
      )}
    </div>
  )
}

/* ─────────────────────────────────────────────
   FAERS HORIZONTAL BAR CHART
───────────────────────────────────────────── */
interface FAERSReaction { name: string; count: number }
interface FAERSGroup { label: string; reactions: FAERSReaction[]; colorVar: string }

function parseFAERSRows(rows: EvidenceRow[], drugA: string, drugB: string): FAERSGroup[] {
  const groups: Record<string, FAERSGroup> = {
    a:     { label: drugA,          reactions: [], colorVar: 'var(--blue)'  },
    b:     { label: drugB,          reactions: [], colorVar: 'var(--cyan)'  },
    combo: { label: 'Combination',  reactions: [], colorVar: 'var(--amber)' },
  }
  for (const row of rows) {
    if (row.title.startsWith('No FAERS')) continue
    // Try meta field first (stable n=12 format), fall back to description prose
    const metaMatch = row.meta?.match(/^n=(\d+)$/)
    const descMatch = row.description.match(/^(\d[\d,]*)/)
    const rawCount = metaMatch ? metaMatch[1] : descMatch ? descMatch[1].replace(/,/g, '') : null
    const count = rawCount !== null ? parseInt(rawCount, 10) : -1
    let key: 'a' | 'b' | 'combo' = 'a'
    let name = row.title
    if (row.title.startsWith('Drug A FAERS reactions: ')) {
      key = 'a'; name = row.title.slice('Drug A FAERS reactions: '.length)
    } else if (row.title.startsWith('Drug B FAERS reactions: ')) {
      key = 'b'; name = row.title.slice('Drug B FAERS reactions: '.length)
    } else if (row.title.startsWith('Combination FAERS reactions: ')) {
      key = 'combo'; name = row.title.slice('Combination FAERS reactions: '.length)
    }
    groups[key].reactions.push({ name, count })
  }
  return Object.values(groups).filter(g => g.reactions.length > 0)
}

function FAERSBarChart({ rows, drugA, drugB }: { rows: EvidenceRow[]; drugA: string; drugB: string }) {
  const groups = parseFAERSRows(rows, drugA, drugB)
  if (groups.length === 0) return <p className="ev-empty">No FAERS reactions returned.</p>
  const maxCount = Math.max(...groups.flatMap(g => g.reactions.map(r => r.count).filter(c => c >= 0)), 1)
  return (
    <div className="faers-chart">
      {groups.map(group => (
        <div className="faers-group" key={group.label}>
          <div className="faers-group-label" style={{ color: group.colorVar }}>{group.label}</div>
          {group.reactions.map(r => (
            <div className="faers-bar-row" key={r.name}>
              <span className="faers-term" title={r.name}>{r.name}</span>
              {r.count >= 0 ? (
                <>
                  <div className="faers-bar-track">
                    <div
                      className="faers-bar-fill"
                      style={{ width: `${Math.max((r.count / maxCount) * 100, 2)}%`, background: group.colorVar }}
                    />
                  </div>
                  <span className="faers-count">{r.count.toLocaleString()}</span>
                </>
              ) : (
                <span className="faers-count" style={{ color: 'var(--muted-light)', fontStyle: 'italic' }}>n/a</span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

/* ─────────────────────────────────────────────
   EVIDENCE CARD SUB-COMPONENT
───────────────────────────────────────────── */
function EvidenceCard({
  evidence, activeTab, drugNames,
}: {
  evidence: EvidenceBundle
  activeTab: EvidenceTab
  drugNames: [string, string]
}) {
  if (activeTab === 'sources') {
    return (
      <div className="ev-section">
        {evidence.sources.map(src => (
          <div className="source-row" key={src.name}>
            <span className={`source-dot ${src.state}`} />
            <div className="source-info">
              <strong>{src.name}</strong>
              <span className={`source-state-label ${src.state}`}>{src.state}</span>
              <p>{src.detail}</p>
            </div>
          </div>
        ))}
      </div>
    )
  }
  if (activeTab === 'references') {
    return (
      <div className="ev-section">
        {evidence.references.map(r => (
          <EvidRow key={r.title} title={r.title} description={r.description} meta={r.meta} />
        ))}
      </div>
    )
  }
  if (activeTab === 'openfda') {
    const card = evidence.openfda
    return (
      <div className="ev-section">
        <MetricGrid metrics={card.metrics} />
        {card.caveat && <div className="ev-caveat">{card.caveat}</div>}
        <FAERSBarChart rows={card.rows} drugA={drugNames[0]} drugB={drugNames[1]} />
      </div>
    )
  }

  if (activeTab === 'overview') {
    const card = evidence.overview
    // Filter out developer-only metrics (evidence mode)
    const clinicalMetrics = card.metrics.filter(m => m.label !== 'Evidence mode')
    return (
      <div className="ev-section">
        <MetricGrid metrics={clinicalMetrics} />
        <div className="ev-sub-heading">PK/PD Profile</div>
        {card.rows.map(r => <EvidRow key={r.title} title={r.title} description={r.description} meta={r.meta} />)}
      </div>
    )
  }

  if (activeTab === 'internal') {
    const card = evidence.internal
    const canonical = card.rows.filter(r => r.meta === 'canonical' || r.title.toLowerCase().includes('canonical'))
    const sideEffects = card.rows.filter(r => r.title.toLowerCase().includes('side-effect'))
    const other = card.rows.filter(r => !canonical.includes(r) && !sideEffects.includes(r))
    return (
      <div className="ev-section">
        <MetricGrid metrics={card.metrics} />
        <EvidenceMethodNotes />
        {canonical.length > 0 && <>
          <div className="ev-sub-heading">Canonical Interaction</div>
          {canonical.map(r => <EvidRow key={r.title} title={r.title} description={r.description} meta={r.meta} />)}
        </>}
        {sideEffects.length > 0 && <>
          <div className="ev-sub-heading">Side-Effect Signals</div>
          {sideEffects.map(r => <EvidRow key={r.title} title={r.title} description={r.description} meta={r.meta} />)}
        </>}
        {other.length > 0 && <>
          <div className="ev-sub-heading">Other Signals</div>
          {other.map(r => <EvidRow key={r.title} title={r.title} description={r.description} meta={r.meta} />)}
        </>}
      </div>
    )
  }

  if (activeTab === 'mechanisms') {
    const card = evidence.mechanisms
    const enzymeRows = card.rows.filter(r => r.meta === 'Enzymes')
    const targetRows = card.rows.filter(r => r.title.toLowerCase().includes('target'))
    const pathwayRows = card.rows.filter(r => r.title.toLowerCase().includes('pathway'))
    const other = card.rows.filter(r => !enzymeRows.includes(r) && !targetRows.includes(r) && !pathwayRows.includes(r))
    return (
      <div className="ev-section">
        <MetricGrid metrics={card.metrics} />
        {enzymeRows.length > 0 && <>
          <div className="ev-sub-heading">Enzyme Interactions</div>
          {enzymeRows.map(r => <EnzymeRow key={r.title} title={r.title} description={r.description} />)}
        </>}
        {targetRows.length > 0 && <>
          <div className="ev-sub-heading">Protein Targets</div>
          {targetRows.map(r => <TagListRow key={r.title} title={r.title} description={r.description} meta={r.meta} />)}
        </>}
        {pathwayRows.length > 0 && <>
          <div className="ev-sub-heading">Pathways</div>
          {pathwayRows.map(r => <TagListRow key={r.title} title={r.title} description={r.description} meta={r.meta} />)}
        </>}
        {other.length > 0 && other.map(r => <EvidRow key={r.title} title={r.title} description={r.description} meta={r.meta} />)}
      </div>
    )
  }

  return null
}

function MetricGrid({ metrics }: { metrics: EvidenceMetric[] }) {
  return (
      <div className="metric-grid">
      {metrics.map(m => (
        <div className={`metric-tile ${m.tone ?? 'neutral'}`} key={m.label}>
          <span>{m.label}</span>
          <strong>{m.value.replace(/\s+/g, ' ')}</strong>
        </div>
      ))}
    </div>
  )
}

function EvidenceMethodNotes() {
  return (
    <div className="method-note-grid" aria-label="Internal evidence metric notes">
      {INTERNAL_EVIDENCE_NOTES.map(note => (
        <details className="method-note" key={note.label}>
          <summary>
            <span>{note.label}</span>
            <strong>{note.title}</strong>
          </summary>
          <p>{note.detail}</p>
          {note.formula && <code>{note.formula}</code>}
          <p>{note.interpretation}</p>
        </details>
      ))}
    </div>
  )
}

function EvidRow({ title, description, meta }: { title:string; description:string; meta?:string }) {
  return (
    <div className="ev-row">
      <div className="ev-row-body">
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
      {meta && <span className="ev-meta">{meta}</span>}
    </div>
  )
}

function EnzymeRow({ title, description }: { title: string; description: string }) {
  const roleColors: Record<string, string> = {
    substrate: 'substrate', inhibitor: 'inhibitor', inducer: 'inducer', transporter: 'transporter'
  }
  const parts = description.split('; ').filter(Boolean)
  return (
    <div className="ev-row">
      <div className="ev-row-body" style={{ width: '100%' }}>
        <strong style={{ display: 'block', marginBottom: 6 }}>{title}</strong>
        <div className="ev-tag-list">
          {parts.flatMap(part => {
            const [role, vals] = part.split(': ')
            const roleClass = roleColors[role?.trim().toLowerCase() ?? ''] ?? ''
            return (vals ?? '').split(', ').filter(Boolean).map(v => (
              <span key={`${role}-${v}`} className={`ev-enzyme-pill ${roleClass}`}>
                <span style={{ opacity: .6, fontSize: 10 }}>{role?.trim()}</span> {v.trim()}
              </span>
            ))
          })}
        </div>
      </div>
    </div>
  )
}

function TagListRow({ title, description, meta }: { title: string; description: string; meta?: string }) {
  const tags = description.split(', ').filter(Boolean)
  return (
    <div className="ev-row">
      <div className="ev-row-body" style={{ width: '100%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
          <strong>{title}</strong>
          {meta && <span className="ev-meta">{meta}</span>}
        </div>
        <div className="ev-tag-list">
          {tags.map(t => <span key={t} className="ev-tag">{t.trim()}</span>)}
        </div>
      </div>
    </div>
  )
}
