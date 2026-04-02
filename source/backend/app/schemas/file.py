from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class UploadUrlRequest(BaseModel):
    filename: str
    file_role: str
    content_type: str = Field(alias='content_type')
    file_size: int


class UploadUrlData(BaseSchema):
    file_id: int
    upload_url: str
    headers: dict[str, str]


class CompleteUploadRequest(BaseModel):
    file_id: int
    checksum_sha256: str | None = None


class FileData(BaseSchema):
    id: int
    file_role: str
    filename: str
    status: str
    file_size: int


class FileDeleteData(BaseSchema):
    file_id: int
    status: str
    message: str
    deleted_at: datetime | None = None
    related_task_count: int = 0
    result_file_unlinked_count: int = 0
