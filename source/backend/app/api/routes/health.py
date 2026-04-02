from fastapi import APIRouter

router = APIRouter(tags=['health'])


@router.get('/health')
def health() -> dict:
    return {'code': 0, 'message': 'ok', 'data': {'status': 'up'}}
