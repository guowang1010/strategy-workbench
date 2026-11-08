# -*- coding: utf-8 -*-
"""应用层异常：与 HTTP 解耦，由 main.py 统一映射成状态码 + 稳定错误码。"""


class AppError(Exception):
    status = 400
    code = 'BAD_REQUEST'

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code


class NotFound(AppError):
    status = 404
    code = 'NOT_FOUND'


class InvalidRequest(AppError):
    status = 422
    code = 'INVALID_REQUEST'


class Busy(AppError):
    """并发/排队超限：任务队列已满。"""
    status = 429
    code = 'TOO_MANY_JOBS'


class Conflict(AppError):
    status = 409
    code = 'CONFLICT'
