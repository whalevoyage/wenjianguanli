/**
 * 极简 Markdown 渲染（自绘，不引第三方库——沿用本项目「不装依赖」的约定）。
 *
 * 只处理 Agent 回复里真正会出现的语法：
 *   代码块 ``` / 无序列表 - * / 有序列表 1. / **粗体** / `行内代码` / 空行分段
 * 其余一律按纯文本处理。
 *
 * 安全：所有文本先转义，再拼装固定标签集，因此可安全地配合 v-html 使用。
 */

const ESCAPES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

/**
 * 转义成可以安全拼进 HTML 的文本。
 * 导出给其他需要 `dangerouslyUseHTMLString` 的地方复用（例如永久删除确认框）：
 * 文件名合法地可以包含 `<` `&` `"`，不转义就是注入面。
 */
export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, char => ESCAPES[char])
}

/** 行内语法：先转义，再替换成固定标签 */
function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

export function renderMarkdown(source) {
  const lines = String(source ?? '').split(/\r?\n/)
  const blocks = []
  let list = null
  let code = null

  const flushList = () => {
    if (!list) return
    const items = list.items.map(item => `<li>${item}</li>`).join('')
    blocks.push(`<${list.type}>${items}</${list.type}>`)
    list = null
  }

  const flushCode = () => {
    if (!code) return
    blocks.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`)
    code = null
  }

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      if (code) flushCode()
      else {
        flushList()
        code = []
      }
      continue
    }

    if (code) {
      code.push(line)
      continue
    }

    if (!line.trim()) {
      flushList()
      continue
    }

    const unordered = line.match(/^\s*[-*]\s+(.*)$/)
    const ordered = line.match(/^\s*\d+[.、)]\s+(.*)$/)

    if (unordered || ordered) {
      const type = unordered ? 'ul' : 'ol'
      if (!list || list.type !== type) {
        flushList()
        list = { type, items: [] }
      }
      list.items.push(inline((unordered || ordered)[1]))
      continue
    }

    flushList()
    blocks.push(`<p>${inline(line)}</p>`)
  }

  flushList()
  flushCode()
  return blocks.join('')
}

export default renderMarkdown
