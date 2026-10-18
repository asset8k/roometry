"""Safe public upload failures; never echo decoder errors or private input."""

from roometry.uploads.schemas import UploadErrorCode, UploadErrorResponse, UploadIssue


class UploadFailure(Exception):
    def __init__(
        self,
        status: int,
        code: UploadErrorCode,
        message: str,
        *,
        file_index: int | None = None,
        field: str | None = None,
    ):
        super().__init__(code)
        self.status = status
        self.response = UploadErrorResponse(
            detail=[
                UploadIssue(
                    code=code, message=message, file_index=file_index, field=field
                )
            ]
        )
