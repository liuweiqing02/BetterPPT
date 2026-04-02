from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import AppException
from app.core.security import CurrentUser
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.file import CompleteUploadRequest, FileData, FileDeleteData, UploadUrlData, UploadUrlRequest
from app.services.file_service import (
    complete_upload,
    delete_file,
    create_upload_slot,
    get_file_by_id,
    open_local_file,
    save_uploaded_bytes,
)

router = APIRouter(prefix='/files', tags=['files'])


@router.post('/upload-url', response_model=APIResponse)
def post_upload_url(
    payload: UploadUrlRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    file, headers = create_upload_slot(
        db,
        user_id=current_user.id,
        filename=payload.filename,
        file_role=payload.file_role,
        content_type=payload.content_type,
        file_size=payload.file_size,
        base_url=str(request.base_url).rstrip('/'),
    )

    data = UploadUrlData(file_id=file.id, upload_url=headers['Upload-Url'], headers={'Content-Type': headers['Content-Type']})
    return APIResponse(data=data)


@router.put('/upload/{file_id}', response_model=APIResponse)
async def put_upload(
    file_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    file = get_file_by_id(db, file_id)
    if not file or file.user_id != current_user.id:
        raise AppException(status_code=404, code=1002, message='file not found')

    content = await request.body()
    if not content:
        raise AppException(status_code=400, code=1001, message='empty upload content')

    save_uploaded_bytes(file, content)
    return APIResponse(data={'file_id': file.id, 'bytes': len(content)})


@router.post('/complete', response_model=APIResponse)
def post_complete_upload(
    payload: CompleteUploadRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    file = complete_upload(
        db,
        user_id=current_user.id,
        file_id=payload.file_id,
        checksum_sha256=payload.checksum_sha256,
    )
    return APIResponse(data=FileData.model_validate(file))


@router.get('/download/{file_id}')
def download_file(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> FileResponse:
    file = get_file_by_id(db, file_id)
    if not file or file.user_id != current_user.id:
        raise AppException(status_code=404, code=1002, message='file not found')

    file_path: Path = open_local_file(file)
    return FileResponse(path=str(file_path), filename=file.filename, media_type=file.mime_type or 'application/octet-stream')


@router.post('/{file_id}/delete', response_model=APIResponse)
def post_delete_file(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    data = delete_file(db, user_id=current_user.id, file_id=file_id)
    return APIResponse(data=FileDeleteData.model_validate(data))
