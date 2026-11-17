import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function MarkdownBody({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    h2: ({ children }) => <h2>{children === 'Bottom Line' ? 'Clinical Summary' : children}</h2>,
    table: ({ children }) => <div className="markdown-table-scroll" role="region" aria-label="Response table" tabIndex={0}>
      <table>{children}</table>
    </div>,
    a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  }}>{text}</ReactMarkdown>
}
