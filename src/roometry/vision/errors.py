"""Safe public failures; never serialize provider messages, bodies, or secrets."""

from roometry.vision.schemas import (
    AnalysisErrorCode,
    AnalysisErrorResponse,
    AnalysisIssue,
)


class AnalysisFailure(Exception):
    def __init__(
        self,
        status: int,
        code: AnalysisErrorCode,
        message: str,
        *,
        diagnostics: dict[str, object] | None = None,
    ):
        super().__init__(code)
        self.diagnostics = diagnostics
        self.status = status
        self.code = code
        self.response = AnalysisErrorResponse(
            detail=[AnalysisIssue(code=code, message=message)]
        )
