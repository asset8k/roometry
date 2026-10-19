"""Safe public failures; never serialize provider messages, bodies, or secrets."""

from roometry.vision.schemas import (
    AnalysisErrorCode,
    AnalysisErrorResponse,
    AnalysisIssue,
)


class AnalysisFailure(Exception):
    def __init__(self, status: int, code: AnalysisErrorCode, message: str):
        super().__init__(code)
        self.status = status
        self.code = code
        self.response = AnalysisErrorResponse(
            detail=[AnalysisIssue(code=code, message=message)]
        )
