// 由日线计算图表指标：均线、唐奇安通道、布林带。纯函数，便于复用与测试。
import { Bar } from './types'

export function ma(vals: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < vals.length; i++) {
    sum += vals[i]
    if (i >= n) sum -= vals[i - n]
    out.push(i >= n - 1 ? sum / n : null)
  }
  return out
}

export function sma(vals: number[], n: number): (number | null)[] {
  return ma(vals, n)
}

export function donchian(highs: number[], lows: number[], n: number) {
  const upper: (number | null)[] = []
  const lower: (number | null)[] = []
  for (let i = 0; i < highs.length; i++) {
    if (i < n - 1) { upper.push(null); lower.push(null); continue }
    let hi = -Infinity, lo = Infinity
    for (let k = i - n + 1; k <= i; k++) { hi = Math.max(hi, highs[k]); lo = Math.min(lo, lows[k]) }
    upper.push(hi); lower.push(lo)
  }
  return { upper, lower }
}

export function bollinger(closes: number[], n: number, k = 2) {
  const mid = sma(closes, n)
  const up: (number | null)[] = []
  const dn: (number | null)[] = []
  for (let i = 0; i < closes.length; i++) {
    const m = mid[i]
    if (m == null) { up.push(null); dn.push(null); continue }
    let s = 0, c = 0
    for (let j = i - n + 1; j <= i; j++) { s += (closes[j] - m) ** 2; c++ }
    const sd = Math.sqrt(s / c)
    up.push(m + k * sd); dn.push(m - k * sd)
  }
  return { mid, up, dn }
}

export function buildIndicators(bars: Bar[]) {
  const closes = bars.map(b => b.adj_close)
  const highs = bars.map(b => b.adj_high)
  const lows = bars.map(b => b.adj_low)
  return {
    ma5: sma(closes, 5),
    ma10: sma(closes, 10),
    ma20: sma(closes, 20),
    ma60: sma(closes, 60),
    don: donchian(highs, lows, 20),
    bol: bollinger(closes, 20, 2),
  }
}
