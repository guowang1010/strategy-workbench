# -*- coding: utf-8 -*-
"""模型清单接口：返回全部模型及其 param_schema / grid，供前端自动渲染参数面板。
另提供 /api/models/rule-meta：自定义规则 DSL 的指标/比较符/时序算子/模板元数据。"""
from fastapi import APIRouter
from engine.models.builtin import model_specs
from engine.rules import INDICATORS, COMPARISONS, TEMPORALS, TEMPLATES, default_rule

router = APIRouter(prefix='/api')


@router.get('/models')
def get_models():
    return {'ok': True, 'data': model_specs()}


@router.get('/models/rule-meta')
def get_rule_meta():
    """前端规则编辑器用它渲染下拉选项与「载入模板」。"""
    return {'ok': True, 'data': {
        'indicators': INDICATORS,
        'comparisons': COMPARISONS,
        'temporals': TEMPORALS,
        'templates': TEMPLATES,
        'default_rule': default_rule(),
    }}
