import { selectionKey, useStore } from '../store'

export default function ResultSelector({ focused, onFocus }: { focused: boolean; onFocus: () => void }) {
  const s = useStore()
  const options = s.run?.results.filter(r => r.code === s.activeCode) || []
  const base = options.find(r => r.model_id === s.activeId)
  const tier = s.run?.viewTier
  const currentTier = tier?.code === s.activeCode && tier.model === s.activeId ? tier.tier : ''
  const stale = s.run && s.runKey !== selectionKey(s.activeDs, s.selected, s.globalParams)
  return <div className="result-selector">
    <span className="result-stock">{s.activeCode || '未选择股票'}</span>
    <label>当前模型<select aria-label="交易明细当前模型" value={base ? s.activeId : ''} disabled={!options.length} onChange={e => s.setActive(e.target.value)}>
      <option value="" disabled>{options.length ? '请选择模型' : '请先运行回测'}</option>
      {options.map(r => <option key={r.model_id} value={r.model_id}>{r.model_id} · {r.name}</option>)}
    </select></label>
    <label>档位<select aria-label="当前结果档位" value={currentTier} disabled={!base?.tiers?.rows.length} onChange={e => {
      const value = e.target.value
      useStore.setState(state => ({ run: state.run ? { ...state.run, viewTier: value && s.activeCode ? { code: s.activeCode, model: s.activeId, tier: value } : null } : null }))
    }}><option value="">原始回测</option>{base?.tiers?.rows.map(t => <option key={t.id} value={t.id} disabled={!t.result}>{t.label}{t.error ? '（不可用）' : ''}</option>)}</select></label>
    <span className={'result-status' + (stale ? ' stale' : '')}>{!s.run ? '尚未回测' : stale ? '参数已变化 · 历史结果' : '已完成回测'}</span>
    <button className="tt-reset" onClick={onFocus}>{focused ? '恢复布局' : '专注结果'}</button>
  </div>
}
