from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, *, status_code: int, code: int, message: str, data: dict | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.data = data or {}
        super().__init__(message)


async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            'code': exc.code,
            'message': exc.message,
            'data': exc.data,
        },
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            'code': 9000,
            'message': 'internal server error',
            'data': {'detail': str(exc)},
        },
    )
