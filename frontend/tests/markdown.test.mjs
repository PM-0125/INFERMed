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
const { MarkdownBody } = await server.ssrLoadModule('/src/MarkdownBody.tsx')
const render = (text) => renderToStaticMarkup(createElement(MarkdownBody, { text }))
const table = '| Mechanism | Meaning | Evidence |\n|---|---|---|\n| **PK** | Different pathways | ev_123 |\n| PD | Shared pathway | Limited |'

test('renders the reported pipe table as a semantic table', () => {
  const html = render(table)
  assert.match(html, /<table/)
  assert.match(html, /<th[^>]*>Mechanism<\/th>/)
  assert.match(html, /<strong>PK<\/strong>/)
  assert.equal((html.match(/<tbody>[\s\S]*<\/tbody>/)?.[0].match(/<tr>/g) || []).length, 2)
  assert.match(html, /ev_123/)
})
test('handles a table between prose and nested lists', () => {
  const html = render(`Context.\n\n${table}\n\n- Monitor\n  - Symptoms\n\n1. Review\n2. Follow up`)
  assert.match(html, /<p>Context\.<\/p>/)
  assert.match(html, /<table/)
  assert.match(html, /<ol>/)
  assert.equal((html.match(/<ul>/g) || []).length, 2)
})
test('preserves escaped pipes and code content', () => {
  const html = render('| Value | Detail |\n|---|---|\n| A\\|B | `CYP3A4` |')
  assert.match(html, /A\|B/)
  assert.match(html, /<code>CYP3A4<\/code>/)
})
test('does not execute raw HTML or javascript links', () => {
  const html = render('<script>alert(1)</script>\n\n[bad](javascript:alert(1))')
  assert.doesNotMatch(html, /<script|href="javascript:/)
})
test('partial streaming tables do not throw; complete tables recover', () => {
  for (let i = 1; i <= table.length; i++) assert.doesNotThrow(() => render(table.slice(0, i)))
  assert.match(render(table), /<table/)
})

test('renders clinical note lists and bold field labels with a clinical heading', () => {
  const html = render('## Bottom Line\n\nSummary.\n\n## Patient Context\n\n- **Supplied age:** 79.\n- **Renal function:** severe impairment.')
  assert.match(html, /<h2>Clinical Summary<\/h2>/)
  assert.doesNotMatch(html, /Bottom Line/)
  assert.match(html, /<ul>/)
  assert.match(html, /<strong>Supplied age:<\/strong>/)
  assert.equal((html.match(/<li>/g) || []).length, 2)
})
