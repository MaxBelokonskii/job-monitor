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


def _view(row: dict) -> dict[str, Any]:
    """Запись библиотеки наружу.

    `stored_name` сюда не попадает: это внутреннее имя файла в каталоге
    данных, а клиент обращается к резюме по идентификатору строки. Имя
    генерируется случайным именно затем, чтобы чужой файл нельзя было
    угадать, — отдавать его наружу значит обесценивать эту меру.
    """
    return {
        "id": row["id"],
        "original_name": row["original_name"],
        "size_bytes": row["size_bytes"],
        "uploaded_at": row["uploaded_at"],
    }


def _filename_from(request: Request) -> str:
    raw_name = request.headers.get(FILENAME_HEADER, "")
    if not raw_name:
        raise HTTPException(
            status_code=400,
            detail=f"не указано имя файла: нужен заголовок {FILENAME_HEADER}",
        )
    return unquote(raw_name)


async def _receive(request: Request, original_name: str) -> tuple[str, int]:
    """Принимает тело потоком и возвращает `(имя на диске, размер)`.

    Куски пишутся на диск по мере прихода, а не копятся в памяти: держать
    десять мегабайт на каждую загрузку незачем, когда файл всё равно едет
    в файл. Предел проверяется по ходу, поэтому слишком большое тело
    обрывается на первом лишнем куске — заголовку `content-length` здесь
    не верят вовсе, он приходит от клиента.
    """
    try:
        with resume_store.receiving(original_name) as incoming:
            async for chunk in request.stream():
                incoming.write(chunk)
            return incoming.commit()
    except resume_store.ResumeRejected as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("")
async def upload_resume(request: Request) -> dict[str, Any]:
    """Принимает файл телом запроса и возвращает его запись в библиотеке.

    Клиентское имя НЕ участвует в построении пути: из него берётся только
    расширение, а имя на диске генерирует `resume_store`. Запись в базу идёт
    после успешной записи на диск — иначе строка могла бы указывать на файл,
    которого нет; а если вставка строки не удалась, файл стирается обратно,
    иначе он остался бы в каталоге навсегда и невидимым для интерфейса.
    """
    original_name = _filename_from(request)
    stored_name, size = await _receive(request, original_name)
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


@router.put("/{resume_id}")
async def replace_resume(resume_id: int, request: Request) -> dict[str, Any]:
    """Меняет файл резюме, сохраняя запись.

    Без этого роута обновить собственное резюме было нельзя: удалить и
    загрузить заново — единственный путь, а удаление резюме, на которое
    ссылается пресет, запрещено (и правильно: пресет остался бы указывать
    в пустоту). Получался тупик на самом обычном действии.

    Идентификатор сохраняется, поэтому пресеты продолжают работать.
    Старый файл стирается ПОСЛЕ успешной замены строки: обратный порядок
    оставил бы запись, указывающую в пустоту, — тот же дефект, что
    закрывало M-3.
    """
    conn = get_connection()
    repo = ResumesRepo(conn)
    previous = repo.get(resume_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="резюме не найдено")

    original_name = _filename_from(request)
    stored_name, size = await _receive(request, original_name)
    try:
        repo.replace_file(resume_id, original_name, stored_name, size, datetime.now())
    except Exception:
        resume_store.remove(stored_name)
        raise
    resume_store.remove(previous["stored_name"])
    return {"id": resume_id, "original_name": original_name, "size_bytes": size}


@router.get("")
async def list_resumes() -> list[dict[str, Any]]:
    return [_view(row) for row in ResumesRepo(get_connection()).list()]


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
