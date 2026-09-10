"""Библиотека резюме: загрузка, список, скачивание, удаление.

**Почему загрузка идёт сырым телом, а не `multipart/form-data`.** Форма
`multipart` требует пакета `python-multipart`, которого нет ни в окружении,
ни в `requirements.lock`. Но дело не только в этом: multipart — это ещё один
разборщик недоверенного ввода на единственном эндпоинте, который принимает
файл извне, и добавлять его ради одного поля незачем. Файл приезжает телом
запроса, имя — заголовком `X-Filename` в процентном кодировании (заголовки
HTTP латиницей, а имя файла может быть каким угодно). Разбирать нечего:
тело — это байты файла, и всё.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from job_monitor import resume_store
from job_monitor.db.connection import get_connection
from job_monitor.db.repositories import ResumesRepo

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

FILENAME_HEADER = "X-Filename"


@router.post("")
async def upload_resume(request: Request) -> dict[str, Any]:
    """Принимает файл телом запроса и возвращает его запись в библиотеке.

    Клиентское имя НЕ участвует в построении пути: из него берётся только
    расширение, а имя на диске генерирует `resume_store`. Запись в базу идёт
    после успешной записи на диск — иначе строка могла бы указывать на файл,
    которого нет; а если вставка строки не удалась, файл стирается обратно,
    иначе он остался бы в каталоге навсегда и невидимым для интерфейса.
    """
    raw_name = request.headers.get(FILENAME_HEADER, "")
    if not raw_name:
        raise HTTPException(
            status_code=400,
            detail=f"не указано имя файла: нужен заголовок {FILENAME_HEADER}",
        )
    original_name = unquote(raw_name)

    # Отказ по объявленному размеру ДО чтения тела: иначе десятки мегабайт
    # сначала окажутся в памяти процесса и только потом будут отвергнуты.
    #
    # Это оптимизация, а не защита, и тестом она не закрепляется: снятие
    # этой проверки не меняет ни кода ответа, ни содержимого — слишком
    # большой файл всё равно отвергает `resume_store.store`. Мутация,
    # убирающая её, проходит зелёной, и это ожидаемо.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > resume_store.MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"файл больше {resume_store.MAX_BYTES // (1024 * 1024)} МБ",
        )

    data = await request.body()
    try:
        stored_name, size = resume_store.store(original_name, data)
    except resume_store.ResumeRejected as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        resume_id = ResumesRepo(get_connection()).add(
            original_name, stored_name, size, datetime.now()
        )
    except Exception:
        # Строки нет — значит файла быть не должно. Иначе он остаётся в
        # каталоге навсегда: в списке его нет, а удалить из интерфейса
        # нечем, потому что интерфейс ходит по идентификаторам строк.
        resume_store.remove(stored_name)
        raise
    return {"id": resume_id, "original_name": original_name, "size_bytes": size}


@router.get("")
async def list_resumes() -> list[dict[str, Any]]:
    return ResumesRepo(get_connection()).list()


@router.get("/{resume_id}/download")
async def download_resume(resume_id: int) -> FileResponse:
    """Отдаёт файл. Аннотация — именно `FileResponse`.

    Аннотация наследником `Response` говорит FastAPI не строить
    `response_model`: валидировать поток байтов схемой нечем, и в OpenAPI
    эндпоинт описывается как файл, а не как JSON.

    Проверено на живом приложении, вопреки очевидному ожиданию: аннотация
    `-> dict` тут НЕ ломает скачивание. FastAPI возвращает объекты
    `Response` как есть, минуя сериализацию, поэтому подмена аннотации
    отдаёт те же 200 и те же байты — мутация проходит зелёной, и тестом это
    не закрепить. Ошибку валидации ответа даёт другой случай: обычный тип
    вроде `dict[str, str]` при возврате настоящего словаря с чужим типом
    значения. Аннотация здесь — вопрос честности контракта, а не работы.
    """
    row = ResumesRepo(get_connection()).get(resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail="резюме не найдено")
    try:
        path = resume_store.path_of(row["stored_name"])
    except resume_store.ResumeRejected as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"файл резюме «{row['original_name']}» пропал из каталога данных",
        )
    return FileResponse(path, filename=row["original_name"])


@router.delete("/{resume_id}")
async def delete_resume(resume_id: int) -> dict[str, str]:
    conn = get_connection()
    repo = ResumesRepo(conn)
    row = repo.get(resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail="резюме не найдено")
    used_by = repo.presets_using(resume_id)
    if used_by:
        # Молчаливая поломка чужого пресета хуже отказа: пресет продолжил бы
        # ссылаться на несуществующий файл, и выяснилось бы это только в
        # момент отправки — без вложения и без объяснения.
        raise HTTPException(
            status_code=400,
            detail="резюме используется пресетами: " + ", ".join(used_by),
        )
    # Сначала строка, потом файл. Обратный порядок оставлял запись,
    # указывающую в пустоту: скачивание такой записи даёт 404, и починить
    # её нечем. Отказ на удалении файла (права, занятость) оставляет файл
    # без строки — это тоже мусор, но безвредный и невидимый, тогда как
    # запись без файла ломает интерфейс.
    repo.delete(resume_id)
    resume_store.remove(row["stored_name"])
    return {"status": "deleted"}
