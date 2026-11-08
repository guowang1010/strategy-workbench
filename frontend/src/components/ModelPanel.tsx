import { useStore } from '../store'
import { ModelSpec, RuleMeta } from '../types'
import RuleEditor from './RuleEditor'

function ParamInput({ spec, mid, value, onChange, ruleMeta }: {
  spec: ModelSpec; mid: string; value: any; onChange: (k: string, v: any) => void;
  ruleMeta: RuleMeta | null
}) {
  const fld = spec.param_schema.find(f => f.key === 'min_hold')
  void fld
  return (
    <div className="param-grid">
      {spec.param_schema.map(f => {
        if (f.type === 'rule') {
          return (
            <div key={f.key} className="rule-field">
              <div className="rule-field-title">{f.label}</div>
              <RuleEditor value={value[f.key] ?? null} onChange={v => onChange(f.key, v)} meta={ruleMeta} />
            </div>
          )
        }
        if (f.allow_none) {
          const on = value[f.key] != null && value[f.key] !== ''
          return (
            <label key={f.key} className="param">
              <span>{f.label}{f.allow_none ? '（不限）' : ''}</span>
              <span className="row">
                <input type="checkbox" checked={on} onChange={e => onChange(f.key, e.target.checked ? f.default : null)} />
                <input type="number" disabled={!on} min={f.min} max={f.max} step={f.step}
                  value={on ? value[f.key] ?? f.default : ''}
                  onChange={e => onChange(f.key, e.target.value === '' ? null : Number(e.target.value))} />
              </span>
            </label>
          )
        }
        if (f.type === 'select' && f.options) {
          return (
            <label key={f.key} className="param">
              <span>{f.label}</span>
              <select value={value[f.key] ?? f.default ?? ''}
                onChange={e => onChange(f.key, e.target.value)}>
                {f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          )
        }
        return (
          <label key={f.key} className="param">
            <span>{f.label}</span>
            <input type="number" min={f.min} max={f.max} step={f.step}
              value={value[f.key] ?? f.default ?? ''}
              onChange={e => onChange(f.key, e.target.value === '' ? null : Number(e.target.value))} />
          </label>
        )
      })}
    </div>
  )
}

export default function ModelPanel() {
  const models = useStore(s => s.models)
  const selected = useStore(s => s.selected)
  const toggleModel = useStore(s => s.toggleModel)
  const setParam = useStore(s => s.setParam)
  const setActive = useStore(s => s.setActive)
  const activeId = useStore(s => s.activeId)
  const globalParams = useStore(s => s.globalParams)
  const setGlobal = useStore(s => s.setGlobal)
  const ruleMeta = useStore(s => s.ruleMeta)

  return (
    <div className="model-panel">
      <details className="global-card">
        <summary>全局参数（本金 / 费用 / 滑点）</summary>
        <div className="param-grid">
          <label className="param"><span>本金(元)</span>
            <input type="number" min={1000} step={1000} value={globalParams.capital}
              onChange={e => setGlobal('capital', Number(e.target.value) || 100000)} /></label>
          <label className="param"><span>佣金率(双边)</span>
            <input type="number" min={0} step={0.00005} value={globalParams.commission}
              onChange={e => setGlobal('commission', Number(e.target.value) || 0)} /></label>
          <label className="param"><span>最低佣金(元)</span>
            <input type="number" min={0} step={1} value={globalParams.min_commission}
              onChange={e => setGlobal('min_commission', Number(e.target.value) || 0)} /></label>
          <label className="param"><span>印花税(卖出)</span>
            <input type="number" min={0} step={0.0001} value={globalParams.stamp_duty}
              onChange={e => setGlobal('stamp_duty', Number(e.target.value) || 0)} /></label>
          <label className="param"><span>过户费(双边)</span>
            <input type="number" min={0} step={0.000001} value={globalParams.transfer_fee}
              onChange={e => setGlobal('transfer_fee', Number(e.target.value) || 0)} /></label>
          <label className="param"><span>滑点%(单边)</span>
            <input type="number" min={0} max={1} step={0.05} value={globalParams.slippage_pct}
              onChange={e => setGlobal('slippage_pct', Math.max(0, Number(e.target.value) || 0))} /></label>
        </div>
        <div className="global-hint">滑点 0 = 按信号价精确成交（原行为）；设 0.1 即买价上浮 0.1%、卖价下压 0.1%，用于压力测试「乐观成交」假设。费用变更影响全部模型与寻优。</div>
      </details>
      {models.map(m => {
        const sel = selected.find(s => s.id === m.id)
        const isActive = activeId === m.id
        return (
          <div key={m.id} className={'model-card' + (isActive ? ' active' : '')}
               onClick={() => sel && setActive(m.id)}>
            <label className="model-head">
              <input type="checkbox" checked={!!sel} onClick={e => e.stopPropagation()}
                onChange={e => toggleModel(m.id, e.target.checked)} />
              <span className="dot" style={{ background: m.color }} />
              <span className="mname">{m.name}</span>
              {!m.tradable && <span className="tag-non">🚫 仅对照</span>}
              {isActive && <span className="tag-active">展示中</span>}
            </label>
            {sel && (
              <ParamInput spec={m} mid={m.id} value={sel.params}
                onChange={(k, v) => setParam(m.id, k, v)} ruleMeta={ruleMeta} />
            )}
          </div>
        )
      })}
    </div>
  )
}
