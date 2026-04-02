from __future__ import annotations

import logging
from dataclasses import dataclass

import redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_STREAM_KEY = 'stream:tasks:pending'
_GROUP_NAME = 'group:tasks:workers'
_LIST_KEY = 'queue:tasks:pending'


@dataclass
class QueueEvent:
    task_no: str
    message_id: str | None = None


_client: redis.Redis | None = None
_queue_mode: str | None = None
_group_ready = False


def get_redis_client() -> redis.Redis | None:
    global _client
    if _client:
        return _client

    settings = get_settings()
    try:
        _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        _client.ping()
    except RedisError as exc:
        logger.warning('redis unavailable: %s', exc)
        _client = None
    return _client


def _detect_queue_mode(client: redis.Redis) -> str:
    probe_key = 'stream:tasks:pending:probe'
    try:
        client.xadd(probe_key, {'probe': '1'}, maxlen=1)
        client.delete(probe_key)
        logger.info('redis queue mode: stream (%s)', _STREAM_KEY)
        return 'stream'
    except Exception:
        logger.warning('redis stream unsupported, fallback queue mode: list (%s)', _LIST_KEY)
        return 'list'


def get_queue_mode() -> str:
    global _queue_mode
    if _queue_mode:
        return _queue_mode

    client = get_redis_client()
    if client is None:
        _queue_mode = 'none'
        return _queue_mode

    _queue_mode = _detect_queue_mode(client)
    return _queue_mode


def ensure_consumer_group() -> None:
    global _group_ready
    if _group_ready:
        return

    client = get_redis_client()
    if client is None:
        return

    if get_queue_mode() != 'stream':
        _group_ready = True
        return

    try:
        client.xgroup_create(_STREAM_KEY, _GROUP_NAME, id='0', mkstream=True)
        _group_ready = True
    except RedisError as exc:
        if 'BUSYGROUP' in str(exc):
            _group_ready = True
            return
        logger.warning('failed to create consumer group: %s', exc)


def enqueue_task(task_no: str) -> bool:
    client = get_redis_client()
    if client is None:
        return False

    mode = get_queue_mode()
    try:
        if mode == 'stream':
            client.xadd(_STREAM_KEY, {'task_no': task_no})
        elif mode == 'list':
            client.rpush(_LIST_KEY, task_no)
        else:
            return False
        return True
    except RedisError as exc:
        logger.warning('enqueue failed: %s', exc)
        return False


def claim_task_from_stream(consumer_name: str, block_ms: int = 5000) -> QueueEvent | None:
    client = get_redis_client()
    if client is None:
        return None

    mode = get_queue_mode()

    if mode == 'stream':
        ensure_consumer_group()
        try:
            records = client.xreadgroup(
                groupname=_GROUP_NAME,
                consumername=consumer_name,
                streams={_STREAM_KEY: '>'},
                count=1,
                block=block_ms,
            )
        except RedisError as exc:
            logger.warning('consume stream failed: %s', exc)
            return None

        if not records:
            return None

        _, stream_records = records[0]
        if not stream_records:
            return None

        message_id, payload = stream_records[0]
        task_no = payload.get('task_no')
        if not task_no:
            return None

        return QueueEvent(task_no=task_no, message_id=message_id)

    if mode == 'list':
        timeout_seconds = max(1, int(block_ms / 1000))
        try:
            item = client.blpop(_LIST_KEY, timeout=timeout_seconds)
        except RedisError as exc:
            logger.warning('consume list failed: %s', exc)
            return None

        if not item:
            return None

        _, task_no = item
        if not task_no:
            return None
        return QueueEvent(task_no=task_no, message_id=None)

    return None


def ack_stream_event(message_id: str) -> None:
    if not message_id:
        return

    client = get_redis_client()
    if client is None:
        return

    if get_queue_mode() != 'stream':
        return

    try:
        client.xack(_STREAM_KEY, _GROUP_NAME, message_id)
    except RedisError as exc:
        logger.warning('ack failed: %s', exc)


def acquire_task_lock(task_no: str, ttl_seconds: int = 600) -> bool:
    client = get_redis_client()
    if client is None:
        return True
    try:
        return bool(client.set(f'lock:task:{task_no}', '1', ex=ttl_seconds, nx=True))
    except RedisError:
        return True


def release_task_lock(task_no: str) -> None:
    client = get_redis_client()
    if client is None:
        return
    try:
        client.delete(f'lock:task:{task_no}')
    except RedisError:
        return


def cache_task_progress(task_no: str, payload: dict, ttl_seconds: int = 72 * 3600) -> None:
    client = get_redis_client()
    if client is None:
        return
    key = f'task:progress:{task_no}'
    try:
        mapping = {k: str(v) for k, v in payload.items() if v is not None}
        for field, value in mapping.items():
            client.hset(key, field, value)
        client.expire(key, ttl_seconds)
    except RedisError:
        return


def push_task_event_cache(task_no: str, event_text: str, ttl_seconds: int = 7 * 24 * 3600) -> None:
    client = get_redis_client()
    if client is None:
        return

    key = f'task:events:{task_no}'
    try:
        client.lpush(key, event_text)
        client.ltrim(key, 0, 499)
        client.expire(key, ttl_seconds)
    except RedisError:
        return


