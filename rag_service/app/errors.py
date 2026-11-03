# 文件说明：定义业务异常和服务不可用异常，并注册统一的 HTTP 错误处理。
# 业务模块抛出明确异常后，接口层将其转换为一致的状态码与错误信息。

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class BusinessError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ServiceUnavailableError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessError)
    async def business_error_handler(
        request: Request, exc: BusinessError
    ) -> JSONResponse:
        return JSONResponse(status_code=200, content={"error": exc.message})

    @app.exception_handler(ServiceUnavailableError)
    async def service_unavailable_handler(
        request: Request, exc: ServiceUnavailableError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": exc.message})
