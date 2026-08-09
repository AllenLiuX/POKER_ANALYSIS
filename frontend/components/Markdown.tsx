"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

/**
 * 暗色主题的 Markdown 渲染（用于 AI 复盘 / 剥削分析报告）。
 * 无 @tailwindcss/typography 依赖，直接给各元素挂 Tailwind class。
 * 报告是流式增量渲染的，react-markdown 每次重解析即可，无需额外处理。
 */
const components: Components = {
  h1: ({ children }) => (
    <h1 className="mb-2 mt-3 text-base font-bold text-neutral-100 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-3 text-sm font-bold text-neutral-100 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-2.5 text-sm font-semibold text-neutral-200 first:mt-0">{children}</h3>
  ),
  p: ({ children }) => (
    <p className="my-1.5 text-sm leading-relaxed text-neutral-200 first:mt-0 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="my-1.5 list-disc space-y-1 pl-5 text-sm leading-relaxed text-neutral-200">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-1.5 list-decimal space-y-1 pl-5 text-sm leading-relaxed text-neutral-200">{children}</ol>
  ),
  li: ({ children }) => <li className="marker:text-neutral-500">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-neutral-50">{children}</strong>,
  em: ({ children }) => <em className="italic text-neutral-300">{children}</em>,
  code: ({ children }) => (
    <code className="rounded bg-neutral-800 px-1 py-0.5 text-[0.85em] text-emerald-300">{children}</code>
  ),
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-emerald-400 underline">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-neutral-700 pl-3 text-neutral-400">{children}</blockquote>
  ),
  hr: () => <hr className="my-3 border-neutral-800" />,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs text-neutral-200">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-neutral-800 bg-neutral-800/50 px-2 py-1 text-left font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border border-neutral-800 px-2 py-1">{children}</td>,
};

export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {children}
    </ReactMarkdown>
  );
}
