import { RuleMeta, RuleNode, RuleCondition, RuleGroup } from '../types'

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T

function isGroup(n: RuleNode): n is RuleGroup {
  return n.$type === 'group'
}

function fmtConst(v: number | null): string {
  if (v == null) return '—'
  return Math.abs(v) >= 10000 ? v.toLocaleString('en-US') : String(v)
}

/** 规则树的中文预览（与后端 rule_text 同构，用于即时反馈） */
export function ruleText(node: RuleNode | null, meta: RuleMeta | null, depth = 0): string {
  if (!node) return '（未配置规则）'
  const ilab = (k: string) => meta?.indicators.find(i => i.key === k)?.label ?? k
  const clab = (k: string) => meta?.comparisons.find(c => c.key === k)?.label ?? k
  if (isGroup(node)) {
    const parts = node.children.map(c => ruleText(c, meta, depth + 1))
    if (!parts.length) return '（空分组）'
    const s = parts.join(node.op === 'all' ? ' 且 ' : ' 或 ')
    return (depth > 0 && parts.length > 1) ? `（${s}）` : s
  }
  const right = node.right_indicator ? ilab(node.right_indicator) : fmtConst(node.right_constant)
  if (node.temporal === 'consecutive') return `${ilab(node.left)} 连续 ${node.periods} 日 ${clab(node.cmp)} ${right}`
  if (node.temporal === 'cross_above') return `${ilab(node.left)} 上穿 ${right}`
  if (node.temporal === 'cross_below') return `${ilab(node.left)} 下穿 ${right}`
  return `${ilab(node.left)} ${clab(node.cmp)} ${right}`
}

function newCond(meta: RuleMeta): RuleCondition {
  return {
    $type: 'condition', left: 'close', cmp: 'gt',
    right_indicator: 'sma20', right_constant: null,
    temporal: 'current', periods: 1,
  }
}

function NodeEditor({ node, meta, onChange, onRemove }: {
  node: RuleNode; meta: RuleMeta; onChange: (v: RuleNode) => void; onRemove?: () => void
}) {
  if (isGroup(node)) {
    const setChild = (i: number, v: RuleNode) => {
      const next = clone(node); next.children[i] = v; onChange(next)
    }
    const removeChild = (i: number) => {
      const next = clone(node); next.children.splice(i, 1); onChange(next)
    }
    return (
      <div className="rule-group">
        <div className="rule-group-head">
          <select value={node.op} onChange={e => onChange({ ...node, op: e.target.value as 'all' | 'any' })}>
            <option value="all">同时满足（AND）</option>
            <option value="any">任一满足（OR）</option>
          </select>
          <button type="button" onClick={() => {
            const next = clone(node); next.children.push(newCond(meta)); onChange(next)
          }}>+ 条件</button>
          <button type="button" onClick={() => {
            const next = clone(node)
            next.children.push({ $type: 'group', op: 'all', children: [newCond(meta)] })
            onChange(next)
          }}>+ 分组</button>
          {onRemove && <button type="button" className="danger" onClick={onRemove}>删除分组</button>}
        </div>
        {node.children.length === 0 && <div className="rule-empty">（空分组，请添加条件）</div>}
        {node.children.map((c, i) => (
          <NodeEditor key={i} node={c} meta={meta}
            onChange={v => setChild(i, v)}
            onRemove={() => removeChild(i)} />
        ))}
      </div>
    )
  }

  const patch = (p: Partial<RuleCondition>) => onChange({ ...node, ...p })
  const setTemporal = (t: string) => {
    const tp = t as RuleCondition['temporal']
    if (tp === 'cross_above' && !['gt', 'gte'].includes(node.cmp)) return patch({ temporal: tp, cmp: 'gt', periods: 1 })
    if (tp === 'cross_below' && !['lt', 'lte'].includes(node.cmp)) return patch({ temporal: tp, cmp: 'lt', periods: 1 })
    return patch({ temporal: tp, periods: tp === 'consecutive' ? Math.max(2, node.periods) : 1 })
  }
  const rightMode = node.right_indicator ? 'indicator' : 'constant'
  const setRightMode = (m: string) => {
    if (m === 'indicator') patch({ right_indicator: 'sma20', right_constant: null })
    else patch({ right_indicator: null, right_constant: 0 })
  }

  return (
    <div className="rule-cond">
      <select value={node.left} onChange={e => patch({ left: e.target.value })}>
        {meta.indicators.map(i => <option key={i.key} value={i.key}>{i.label}</option>)}
      </select>
      <select value={node.temporal} onChange={e => setTemporal(e.target.value)}>
        {meta.temporals.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
      </select>
      {node.temporal !== 'cross_above' && node.temporal !== 'cross_below' && (
        <select value={node.cmp} onChange={e => patch({ cmp: e.target.value as RuleCondition['cmp'] })}>
          {meta.comparisons.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
      )}
      <select value={rightMode} onChange={e => setRightMode(e.target.value)}>
        <option value="indicator">对比指标</option>
        <option value="constant">对比常量</option>
      </select>
      {rightMode === 'indicator' ? (
        <select value={node.right_indicator ?? 'sma20'}
          onChange={e => patch({ right_indicator: e.target.value })}>
          {meta.indicators.map(i => <option key={i.key} value={i.key}>{i.label}</option>)}
        </select>
      ) : (
        <input type="number" step="any" value={node.right_constant ?? 0}
          onChange={e => patch({ right_constant: e.target.value === '' ? null : Number(e.target.value) })} />
      )}
      {node.temporal === 'consecutive' && (
        <span className="rule-periods">
          连续 <input type="number" min={1} max={60} step={1} value={node.periods}
            onChange={e => patch({ periods: Math.max(1, Math.min(60, Number(e.target.value) || 1)) })} /> 日
        </span>
      )}
      {onRemove && <button type="button" className="danger" onClick={onRemove}>删除</button>}
    </div>
  )
}

export default function RuleEditor({ value, onChange, meta }: {
  value: RuleNode | null; onChange: (v: RuleNode) => void; meta: RuleMeta | null
}) {
  if (!meta) return <div className="rule-empty">规则元数据加载中…</div>
  const root: RuleNode = value ?? meta.default_rule

  return (
    <div className="rule-editor">
      <div className="rule-templates">
        <span>模板</span>
        <select value="" onChange={e => {
          const t = meta.templates.find(x => x.name === e.target.value)
          if (t) onChange(clone(t.rule))
        }}>
          <option value="">载入模板…</option>
          {meta.templates.map(t => <option key={t.name} value={t.name}>{t.name}</option>)}
        </select>
        <button type="button" onClick={() => onChange(clone(meta.default_rule))}>重置</button>
      </div>
      <NodeEditor node={root} meta={meta} onChange={onChange} />
      <div className="rule-preview">当前规则：{ruleText(root, meta)}</div>
    </div>
  )
}
