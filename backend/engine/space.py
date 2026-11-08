# -*- coding: utf-8 -*-
"""搜索空间解析与校验。

维度描述支持两种写法：

1) **显式对象**（推荐，语义无歧义）：
   - `{"values": [...]}`：枚举，逐值展开（字符串或有限数值）；
   - `{"range": [lo, hi, step]}` / `{"range": [lo, hi]}`：闭区间等差（step 缺省 1）。

2) **裸数组**（旧语义，仅为兼容保留，不要在新代码里依赖）：
   - 全字符串 → 枚举（长度无关）；
   - 3 个数值 [lo, hi, step]：闭区间等差，要求有限数值、step > 0、lo <= hi；
   - 2 个整数 [lo, hi]：闭区间整数步长 1；
   - 其余：显式枚举值（数值须有限）。

历史实现「3 个元素=区间、2 个=range、其他=枚举」在 R4 前的后果：`entry` 缩到 3 个
字符串会被当成区间而报错，数值枚举 `[2, 6, 10]` 会被当成区间只取到 1 个值。
现在字符串数组一律按枚举处理，数值数组仍按旧语义（避免破坏既有网格），
需要「三数值枚举」请显式写 `{"values": [2, 6, 10]}`。

维度值个数、总组合数、参数名均设上限，超限抛 SpaceError（API 映射 422）。
"""
import math

MAX_VALUES = 500          # 单维最多取值个数
MAX_COMBOS = 30000        # 总组合数上限（M4 默认网格 4×20×15×15 = 18000）


class SpaceError(ValueError):
    """搜索空间非法。API 层统一映射为 422，不做静默截断。"""


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v, name):
    if not _is_num(v):
        raise SpaceError(f'{name} 取值必须为数值，收到 {v!r}')
    f = float(v)
    if not math.isfinite(f):
        raise SpaceError(f'{name} 取值必须为有限数值，收到 {v!r}')
    return f


def _dedupe_enum(vals, name: str) -> list:
    """校验枚举值并去重保序（重复值只会白跑组合）。"""
    out, seen = [], set()
    for v in vals:
        if isinstance(v, str):
            if not v.strip():
                raise SpaceError(f'{name} 含空字符串取值')
            key = ('str', v)
        else:
            key = ('num', _finite(v, name))
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    if len(out) > MAX_VALUES:
        raise SpaceError(f'{name} 取值过多（{len(out)} 个 > 上限 {MAX_VALUES}）')
    return out


def _expand_range(spec, name: str, compat: bool = False) -> list:
    """闭区间等差展开。spec 为 [lo, hi, step] 或 [lo, hi]（整数步长 1）。

    compat=True 表示该 spec 来自旧式裸数组（语义靠长度猜），此时对「步长大于区间宽度、
    实际只会得到 1 个取值」的写法直接报错并提示改用显式 values，避免静默丢值（R4）。
    """
    if len(spec) == 2:
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in spec):
            raise SpaceError(f'{name} 的 range 两元素形式必须为整数 [lo, hi]；'
                             f'需要小数步长请写 [lo, hi, step]')
        lo, hi = spec
        if lo > hi:
            raise SpaceError(f'{name} 的下界不能大于上界（{lo} > {hi}）')
        if hi - lo + 1 > MAX_VALUES:
            raise SpaceError(f'{name} 取值过多（{hi - lo + 1} 个 > 上限 {MAX_VALUES}）')
        return list(range(lo, hi + 1))

    lo, hi, step = (_finite(v, name) for v in spec)
    if step <= 0:
        raise SpaceError(f'{name} 的步长必须大于 0，收到 {spec[2]!r}')
    if lo > hi:
        raise SpaceError(f'{name} 的下界不能大于上界（{lo} > {hi}）')
    if compat and lo != hi and step > (hi - lo) + 1e-12:
        raise SpaceError(
            f'{name} 的数组 {list(spec)!r} 在旧语义里是「区间 [下界, 上界, 步长]」，'
            f'但步长大于区间宽度，只会得到 1 个取值。若想枚举这几个值，请写 '
            f'{{"values": {list(spec)!r}}}；若确实只取单个值，请写 {{"range": {list(spec)!r}}}')
    count = int(math.floor((hi - lo) / step + 1e-9)) + 1
    if count > MAX_VALUES:
        raise SpaceError(f'{name} 取值过多（{count} 个 > 上限 {MAX_VALUES}），请放大步长')
    return [round(lo + step * i, 10) for i in range(count)]


def _expand_dict(spec: dict, name: str) -> list:
    keys = set(spec)
    if 'values' in keys and 'range' in keys:
        raise SpaceError(f'{name} 不能同时给出 values 与 range')
    if 'values' in keys:
        vals = spec['values']
        if not isinstance(vals, (list, tuple)) or not vals:
            raise SpaceError(f'{name} 的 values 必须是非空数组')
        if len(vals) > MAX_VALUES:
            raise SpaceError(f'{name} 取值过多（{len(vals)} 个 > 上限 {MAX_VALUES}）')
        return _dedupe_enum(vals, name)
    if 'range' in keys:
        rng = spec['range']
        if not isinstance(rng, (list, tuple)) or len(rng) not in (2, 3):
            raise SpaceError(f'{name} 的 range 必须是 [lo, hi] 或 [lo, hi, step]，收到 {rng!r}')
        return _expand_range(rng, name)
    raise SpaceError(f'{name} 的对象描述只支持 values / range，收到键：{sorted(keys)}')


def expand(spec, name: str = '参数') -> list:
    """把单个维度的描述展开为取值列表（严格校验，不做宽容兜底）。"""
    if isinstance(spec, dict):
        return _expand_dict(spec, name)
    if not isinstance(spec, (list, tuple)):
        raise SpaceError(f'{name} 的搜索空间必须是数组或 {{values|range}} 对象，'
                         f'收到 {type(spec).__name__}')
    if len(spec) == 0:
        raise SpaceError(f'{name} 的搜索空间不能为空')

    # 全字符串：枚举，与长度无关（三元素不会掉进区间分支）
    if all(isinstance(v, str) for v in spec):
        return _dedupe_enum(spec, name)

    if len(spec) == 3:
        return _expand_range(spec, name, compat=True)
    if len(spec) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in spec):
        return _expand_range(spec, name)
    return _dedupe_enum(spec, name)


def parse_space(space, allowed=None, max_combos: int = MAX_COMBOS) -> dict:
    """校验并展开整个搜索空间，返回 {参数名: [取值, ...]}。"""
    if not isinstance(space, dict):
        raise SpaceError('搜索空间必须是对象（参数名 → 取值数组）')
    if not space:
        raise SpaceError('搜索空间不能为空')
    if len(space) > 12:
        raise SpaceError(f'搜索维度过多（{len(space)} > 12）')

    out = {}
    for key, spec in space.items():
        if not isinstance(key, str) or not key.strip():
            raise SpaceError(f'参数名非法: {key!r}')
        if allowed is not None and key not in allowed:
            raise SpaceError(f'未知参数 "{key}"，可用参数：{", ".join(sorted(allowed))}')
        out[key] = expand(spec, key)

    total = 1
    for vals in out.values():
        total *= len(vals)
        if total > max_combos:
            raise SpaceError(f'搜索组合数超过上限 {max_combos}，请缩小搜索空间')
    return out


def count_combos(parsed: dict) -> int:
    n = 1
    for vals in parsed.values():
        n *= len(vals)
    return n if parsed else 0
