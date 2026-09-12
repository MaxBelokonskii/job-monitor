# Пошаговое обучение по интерфейсу — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОДНАВЫК: используйте
> superpowers:subagent-driven-development или superpowers:executing-plans и
> выполняйте план задача за задачей. Шаги помечены `- [ ]` для отметок.

**Цель:** затемнение экрана с подсветкой одного элемента и карточкой-подсказкой,
проводящее пользователя от выбора площадок до кнопок запуска воркеров.

**Архитектура:** новый `frontend/tour.js` подключается **перед** `app.js`.
Геометрия — чистые функции без DOM, проверяемые под node. Сценарий — массив
данных, каждая цель которого обязана существовать в `index.html`. Из `app.js`
выносится `switchPage(page)`, которую тур вызывает вместо своей копии.

**Стек:** ванильный ES2020 без сборки и без зависимостей; тесты — pytest,
разбирающий исходники текстом, и node для чистых функций.

**Спецификация:** `docs/superpowers/specs/2026-09-12-onboarding-tour-design.md`

**Ветка:** `feature/onboarding-tour`

## Глобальные ограничения

- `script-src 'self'` без `unsafe-inline`: ни одного атрибута `on*=`, ни одного
  инлайнового `<script>`. Все кнопки — через `data-action`.
- Ни одного построения разметки строками: только `el()` / `fill()` из `app.js`.
- Никаких внешних зависимостей фронтенда и никаких CDN.
- Сервер слушает только `127.0.0.1`; состояние — через `job_monitor/paths.py`.
- Порядок подключения строго `tour.js`, затем `app.js` (обоснование — раздел 4
  спецификации).
- `tour.js` обязан попасть в `VERSIONED_ASSETS` в `api/main.py`.
- Существующие тесты не ослабляются. `tests/test_frontend_safety.py` — растяжка
  из плана укрепления, её не трогать вовсе.
- Тексты интерфейса — на русском.
- Каждое сообщение коммита заканчивается строкой
  `Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB`.
- Полный прогон — `make test`. Он должен быть зелёным после каждой задачи.

---

### Задача 1: `switchPage(page)` — вынести из замыкания

**Файлы:**
- Изменить: `frontend/app.js:303-314`
- Тест: `tests/test_frontend_tour.py` (создаётся здесь)

**Интерфейсы:**
- Отдаёт: `async function switchPage(page)` — снимает `active` со всех страниц и
  пунктов навигации, ставит его странице `page-<page>` и пункту
  `.nav-item[data-page="<page>"]`, затем ждёт загрузчик из `PAGE_LOADERS[page]`.
  Неизвестное имя страницы — тихий выход без изменений.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_frontend_tour.py`:

```python
"""Инварианты пошагового обучения.

Тур — это копия знаний о вёрстке, живущая отдельно от вёрстки. Такая копия
расходится с оригиналом молча: переименовали `id` — тур подсвечивает пустоту,
и ни один существующий тест этого не заметит. Поэтому связь «шаг → элемент»
проверяется здесь, а геометрия затемнения отделена от DOM и исполняется под
node.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import requires_node

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"
APP_JS = FRONTEND_DIR / "app.js"
TOUR_JS = FRONTEND_DIR / "tour.js"

skip_without_node = requires_node


def _index_source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _tour_source() -> str:
    return TOUR_JS.read_text(encoding="utf-8")


def _extract_function_source(source: str, name: str) -> str:
    """Исходник функции верхнего уровня — по соглашению файлов фронтенда
    такая функция закрывается `}` в нулевой колонке."""
    match = re.search(
        rf"^(?:async )?function {name}\(.*?^\}}", source, re.DOTALL | re.MULTILINE
    )
    assert match, f"функция {name} не найдена"
    return match.group(0)


# ── switchPage: одна реализация на навигацию и на тур ──────────────────


def test_page_switching_is_a_function_not_a_closure() -> None:
    """Тур обязан переключать вкладку той же функцией, что и клик по
    навигации. Пока переключение жило внутри обработчика, вызвать его было
    неоткуда, и тур завёл бы вторую копию — расходящуюся при первой же
    правке навигации."""
    source = _app_source()
    assert re.search(r"^async function switchPage\(page\)", source, re.MULTILINE), (
        "switchPage не объявлена как функция верхнего уровня в app.js"
    )


def test_the_nav_handler_delegates_instead_of_repeating_itself() -> None:
    """Обработчик `.nav-item` не должен сам снимать и ставить `active`:
    иначе правка в switchPage не дойдёт до кликов по навигации."""
    source = _app_source()
    handler = re.search(
        r"document\.querySelectorAll\('\.nav-item\[data-page\]'\)"
        r"\.forEach\(item => \{(.*?)\n\}\);",
        source,
        re.DOTALL,
    )
    assert handler, "обработчик навигации не найден — его форма изменилась?"
    body = handler.group(1)
    assert "switchPage" in body, "обработчик навигации не зовёт switchPage"
    assert "classList" not in body, (
        "обработчик навигации снова сам правит классы — это копия switchPage"
    )
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: FAILED — `switchPage не объявлена как функция верхнего уровня`.

- [ ] **Шаг 3: Реализовать**

В `frontend/app.js` заменить блок на строках 303–314 целиком:

```javascript
// Переключение страниц — функция, а не тело обработчика: её вызывает и клик
// по навигации, и пошаговое обучение (frontend/tour.js), которому надо
// привести пользователя на нужный экран. Пока логика жила внутри замыкания,
// у тура не было выбора, кроме как завести вторую копию.
async function switchPage(page) {
  const item = document.querySelector(`.nav-item[data-page="${page}"]`);
  const target = document.getElementById('page-' + page);
  if (!item || !target) return;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  target.classList.add('active');
  item.classList.add('active');
  const load = PAGE_LOADERS[page];
  if (load) await load();
}

document.querySelectorAll('.nav-item[data-page]').forEach(item => {
  item.addEventListener('click', () => switchPage(item.dataset.page));
});
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: 2 passed.

- [ ] **Шаг 5: Полный прогон**

Запустить: `make test`
Ожидается: зелёный. `test_every_screen_has_a_loader_entry` и
`test_every_nav_item_has_a_page_and_every_page_has_a_nav_item` в
`tests/test_frontend_screens.py` читают `PAGE_LOADERS` и разметку — они не
должны сломаться.

- [ ] **Шаг 6: Коммит**

```bash
git add frontend/app.js tests/test_frontend_tour.py
git commit -m "$(cat <<'EOF'
refactor(ui): переключение страниц стало функцией switchPage

Логика жила внутри обработчика клика по .nav-item, и вызвать её было
неоткуда. Пошаговому обучению нужно приводить пользователя на нужный
экран; без вынесения оно завело бы вторую копию, расходящуюся с первой
при любой правке навигации.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
EOF
)"
```

---

### Задача 2: геометрия затемнения

**Файлы:**
- Создать: `frontend/tour.js`
- Изменить: `frontend/index.html` (тег `<script>` перед `app.js`)
- Изменить: `api/main.py:195` (`VERSIONED_ASSETS`)
- Тест: `tests/test_frontend_tour.py`

**Интерфейсы:**
- Отдаёт: `clampRect(rect, viewport)`, `maskRects(hole, viewport)`,
  `cardPosition(hole, cardSize, viewport)`. Прямоугольник везде —
  `{x, y, width, height}`; `viewport` и `cardSize` — `{width, height}`.
  `maskRects` возвращает `{top, bottom, left, right}` из четырёх таких же
  прямоугольников. `cardPosition` возвращает `{x, y}`. `hole === null` значит
  «цели нет»: затемнён весь экран, карточка по центру.

- [ ] **Шаг 1: Написать падающие тесты**

Дописать в `tests/test_frontend_tour.py`:

```python
# ── Геометрия затемнения ──────────────────────────────────────────────
#
# Затемнение собрано из четырёх прямоугольников, а не из одной тени
# (`box-shadow: 0 0 0 9999px`), потому что тень не делает дырки в попадании
# курсора: подсвеченную кнопку было бы нельзя нажать, а сквозной клик
# проходил бы и по затемнённой навигации, уводя страницу из-под тура.

GEOMETRY = ("clampRect", "maskRects", "cardPosition")


def _geometry_source() -> str:
    source = _tour_source()
    constants = re.search(r"^const TOUR_GAP = \d+;", source, re.MULTILINE)
    assert constants, "TOUR_GAP не найдена в tour.js"
    return "\n".join(
        [constants.group(0)]
        + [_extract_function_source(source, name) for name in GEOMETRY]
    )


def _run_node(script: str):
    import subprocess

    return subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )


VIEWPORT = {"width": 1280, "height": 800}
CARD = {"width": 340, "height": 260}

# Цель в середине, у каждого края и в каждом углу: ровно те положения, где
# прямоугольник маски может выродиться в отрицательный размер.
HOLES = {
    "середина": {"x": 500, "y": 350, "width": 280, "height": 90},
    "у левого края": {"x": 0, "y": 350, "width": 280, "height": 90},
    "у правого края": {"x": 1000, "y": 350, "width": 280, "height": 90},
    "у верхнего края": {"x": 500, "y": 0, "width": 280, "height": 90},
    "у нижнего края": {"x": 500, "y": 710, "width": 280, "height": 90},
    "левый верхний угол": {"x": 0, "y": 0, "width": 280, "height": 90},
    "правый нижний угол": {"x": 1000, "y": 710, "width": 280, "height": 90},
    "выходит за экран": {"x": 1200, "y": 760, "width": 400, "height": 200},
}


@skip_without_node
def test_the_four_masks_cover_the_screen_except_the_hole() -> None:
    """Суммарная площадь масок обязана равняться площади экрана минус дырка.

    Проверяется площадью И пересечениями (следующий тест): по одной площади
    сдвинутая маска прошла бы, а именно сдвиг и оставляет светлую полосу.
    """
    script = "\n".join((
        _geometry_source(),
        f"const viewport = {json.dumps(VIEWPORT)};",
        f"const holes = {json.dumps(HOLES, ensure_ascii=False)};",
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        const area = r => r.width * r.height;
        for (const [name, raw] of Object.entries(holes)) {
          const hole = clampRect(raw, viewport);
          const m = maskRects(raw, viewport);
          const parts = [m.top, m.bottom, m.left, m.right];
          for (const p of parts) {
            check(name + ': маска не отрицательна', p.width >= 0 && p.height >= 0);
          }
          const covered = parts.reduce((sum, p) => sum + area(p), 0);
          const expected = viewport.width * viewport.height - area(hole);
          check(name + ': сумма масок = экран минус дырка (' + covered + ' vs ' + expected + ')',
                Math.abs(covered - expected) < 0.5);
        }
        const whole = maskRects(null, viewport);
        check('без цели экран затемнён целиком',
              area(whole.top) === viewport.width * viewport.height);
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@skip_without_node
def test_no_mask_overlaps_the_hole() -> None:
    """Обратная сторона проверки площадью: ни один прямоугольник затемнения
    не накрывает подсвеченный элемент, иначе он окажется под плёнкой и
    перестанет нажиматься."""
    script = "\n".join((
        _geometry_source(),
        f"const viewport = {json.dumps(VIEWPORT)};",
        f"const holes = {json.dumps(HOLES, ensure_ascii=False)};",
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        function overlaps(a, b) {
          return a.x < b.x + b.width && b.x < a.x + a.width
              && a.y < b.y + b.height && b.y < a.y + a.height;
        }
        for (const [name, raw] of Object.entries(holes)) {
          const hole = clampRect(raw, viewport);
          if (hole.width === 0 || hole.height === 0) continue;
          const m = maskRects(raw, viewport);
          for (const [side, rect] of Object.entries(m)) {
            check(name + ': маска ' + side + ' накрывает дырку', !overlaps(rect, hole));
          }
        }
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


@skip_without_node
def test_the_card_stays_on_screen_and_off_the_hole() -> None:
    """Карточка целиком внутри экрана при любом положении цели и не
    накрывает подсвеченный элемент.

    Накрыть его значит сделать невозможным то, ради чего всё затевалось:
    читать инструкцию и выполнять её одновременно. Гарантия даётся для
    целей не выше 40% экрана — все цели сценария такие; для вырожденного
    случая «цель во весь экран» проверяется только попадание в экран.
    """
    script = "\n".join((
        _geometry_source(),
        f"const viewport = {json.dumps(VIEWPORT)};",
        f"const card = {json.dumps(CARD)};",
        f"const holes = {json.dumps(HOLES, ensure_ascii=False)};",
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        function overlaps(a, b) {
          return a.x < b.x + b.width && b.x < a.x + a.width
              && a.y < b.y + b.height && b.y < a.y + a.height;
        }
        const cases = Object.entries(holes).concat([['без цели', null]]);
        for (const [name, raw] of cases) {
          const pos = cardPosition(raw, card, viewport);
          const box = { x: pos.x, y: pos.y, width: card.width, height: card.height };
          check(name + ': карточка не левее экрана', box.x >= 0);
          check(name + ': карточка не выше экрана', box.y >= 0);
          check(name + ': карточка не правее экрана', box.x + box.width <= viewport.width);
          check(name + ': карточка не ниже экрана', box.y + box.height <= viewport.height);
          if (raw) {
            const hole = clampRect(raw, viewport);
            check(name + ': карточка не накрывает цель', !overlaps(box, hole));
          }
        }
        const huge = { x: 0, y: 0, width: viewport.width, height: viewport.height };
        const pos = cardPosition(huge, card, viewport);
        check('цель во весь экран: карточка всё равно в экране',
              pos.x >= 0 && pos.y >= 0
              && pos.x + card.width <= viewport.width
              && pos.y + card.height <= viewport.height);
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_the_tour_script_is_versioned_like_the_others() -> None:
    """Отпечаток статики считается по списку файлов в api/main.py. Файл,
    забытый в этом списке, браузер будет отдавать из кэша и после правки —
    ровно тот дефект, из-за которого правки «не приезжали» в app.js."""
    main_py = Path(__file__).resolve().parents[1] / "api" / "main.py"
    source = main_py.read_text(encoding="utf-8")
    versioned = re.search(r"VERSIONED_ASSETS = \(([^)]*)\)", source)
    assert versioned, "VERSIONED_ASSETS не найдена в api/main.py"
    assert '"tour.js"' in versioned.group(1), (
        "tour.js не участвует в отпечатке статики — правки тура не дойдут до браузера"
    )


def test_the_tour_script_loads_before_the_app() -> None:
    """ACTIONS в app.js — объектный литерал сокращённой записи, и имена
    функций тура должны быть объявлены к моменту его вычисления."""
    source = _index_source()
    tour_at = source.find("tour.js")
    app_at = source.find("app.js")
    assert tour_at != -1, "tour.js не подключён в index.html"
    assert app_at != -1, "app.js не подключён в index.html"
    assert tour_at < app_at, (
        "tour.js подключён после app.js — ACTIONS не увидит его функций"
    )
    assert "tour.js?v=__ASSET_VERSION__" in source, (
        "адрес tour.js без версии — браузер отдаст его из кэша после правки"
    )
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: FAILED — `frontend/tour.js` не существует (`FileNotFoundError`), и
две текстовые проверки про подключение и версию.

- [ ] **Шаг 3: Создать `frontend/tour.js` с геометрией**

```javascript
// ── Пошаговое обучение ────────────────────────────────────────────────
// Подключается ПЕРЕД app.js: ACTIONS в app.js — объектный литерал
// сокращённой записи, и функции тура должны быть объявлены к моменту его
// вычисления. Обратной зависимости это не создаёт: el, fill и switchPage —
// объявления функций, они попадают в глобальную область и доступны к
// моменту, когда тур их действительно вызывает.

const TOUR_PADDING = 6;   // на сколько дырка шире самой цели
const TOUR_GAP = 12;      // зазор между дыркой и карточкой

// Прямоугольник, обрезанный по экрану. Цель может уходить за край —
// частично прокручена или шире окна, — и тогда маска без обрезки получает
// отрицательный размер, а `width: -40px` браузер молча игнорирует, оставляя
// незатемнённую полосу.
function clampRect(rect, viewport) {
  const x1 = Math.max(0, Math.min(rect.x, viewport.width));
  const y1 = Math.max(0, Math.min(rect.y, viewport.height));
  const x2 = Math.max(0, Math.min(rect.x + rect.width, viewport.width));
  const y2 = Math.max(0, Math.min(rect.y + rect.height, viewport.height));
  return { x: x1, y: y1, width: Math.max(0, x2 - x1), height: Math.max(0, y2 - y1) };
}

// Четыре прямоугольника затемнения вокруг дырки. Именно четыре, а не одна
// тень: `box-shadow: 0 0 0 9999px` рисует затемнение, но попадание курсора
// считается по границам самого элемента, и дырки в нём не возникает.
function maskRects(hole, viewport) {
  const W = viewport.width;
  const H = viewport.height;
  if (!hole) {
    const none = { x: 0, y: 0, width: 0, height: 0 };
    return { top: { x: 0, y: 0, width: W, height: H }, bottom: none, left: none, right: none };
  }
  const h = clampRect(hole, viewport);
  const bottomY = h.y + h.height;
  const rightX = h.x + h.width;
  return {
    top: { x: 0, y: 0, width: W, height: h.y },
    bottom: { x: 0, y: bottomY, width: W, height: H - bottomY },
    left: { x: 0, y: h.y, width: h.x, height: h.height },
    right: { x: rightX, y: h.y, width: W - rightX, height: h.height },
  };
}

// Куда поставить карточку: под целью, над ней или сбоку — первое, что
// помещается. Карточка поверх подсвеченного поля означала бы, что читать
// инструкцию и выполнять её одновременно нельзя.
function cardPosition(hole, cardSize, viewport) {
  const W = viewport.width;
  const H = viewport.height;
  const cw = cardSize.width;
  const ch = cardSize.height;
  if (!hole) {
    return { x: Math.max(0, (W - cw) / 2), y: Math.max(0, (H - ch) / 2) };
  }
  const h = clampRect(hole, viewport);
  const alignX = Math.min(Math.max(0, h.x), Math.max(0, W - cw));
  const alignY = Math.min(Math.max(0, h.y), Math.max(0, H - ch));
  const below = h.y + h.height + TOUR_GAP;
  if (below + ch <= H) return { x: alignX, y: below };
  const above = h.y - TOUR_GAP - ch;
  if (above >= 0) return { x: alignX, y: above };
  // Цель высокая: снизу и сверху места нет. Сбоку почти всегда есть —
  // рабочая ширина приложения от 900px, карточка 340px.
  const right = h.x + h.width + TOUR_GAP;
  if (right + cw <= W) return { x: right, y: alignY };
  const left = h.x - TOUR_GAP - cw;
  if (left >= 0) return { x: left, y: alignY };
  // Цель во весь экран: перекрытие неизбежно, держим карточку хотя бы в
  // границах окна.
  return { x: Math.max(0, W - cw), y: Math.max(0, H - ch) };
}
```

- [ ] **Шаг 4: Подключить файл**

В `frontend/index.html` заменить единственный тег `<script>` на два, в этом
порядке:

```html
<script src="/static/tour.js?v=__ASSET_VERSION__"></script>
<script src="/static/app.js?v=__ASSET_VERSION__"></script>
```

В `api/main.py:195`:

```python
VERSIONED_ASSETS = ("app.js", "tour.js", "style.css")
```

- [ ] **Шаг 5: Убедиться, что тесты проходят**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: 7 passed (без node — 4 passed, 3 skipped).

- [ ] **Шаг 6: Полный прогон**

Запустить: `make test`
Ожидается: зелёный.

- [ ] **Шаг 7: Коммит**

```bash
git add frontend/tour.js frontend/index.html api/main.py tests/test_frontend_tour.py
git commit -m "$(cat <<'EOF'
feat(ui): геометрия затемнения для пошагового обучения

Четыре прямоугольника вокруг дырки, а не box-shadow: тень рисует
затемнение, но попадание курсора считается по границам самого элемента,
и дырки в нём не возникает. Подсвеченную кнопку было бы нельзя нажать.

Прямоугольники обрезаются по экрану: цель, уходящая за край, давала
отрицательный размер маски, а такой размер браузер молча игнорирует,
оставляя незатемнённую полосу.

tour.js подключён перед app.js и добавлен в VERSIONED_ASSETS.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
EOF
)"
```

---

### Задача 3: сценарий и цели подсветки

**Файлы:**
- Изменить: `frontend/tour.js` (дописать `TOUR_STEPS`, `stepsFor`)
- Изменить: `frontend/index.html` (одиннадцать новых `id`)
- Тест: `tests/test_frontend_tour.py`

**Интерфейсы:**
- Отдаёт: `TOUR_STEPS` — массив из 14 объектов строго вида
  `{ page, target, platforms, title, text }`, где `page` — `'overview'`,
  `'settings'` или `null`; `target` — CSS-селектор или `null`; `platforms` —
  `'all'`, `'tg'` или `'hh'`; `text` — массив строк-абзацев. У нулевого шага
  дополнительно `choices: true`.
- Отдаёт: `stepsFor(platforms)` — принимает массив вроде `['tg', 'hh']`,
  возвращает отфильтрованный массив шагов.

- [ ] **Шаг 1: Написать падающие тесты**

Дописать в `tests/test_frontend_tour.py`:

```python
# ── Сценарий ──────────────────────────────────────────────────────────

STEPS_BLOCK = re.compile(r"^const TOUR_STEPS = \[(.*?)^\];", re.DOTALL | re.MULTILINE)
STEP_ENTRY = re.compile(
    r"\{\s*page:\s*(null|'[a-z]+'),"
    r"\s*target:\s*(null|'[^']+'),"
    r"\s*platforms:\s*'([a-z]+)'"
)
ID_ATTR = re.compile(r'\bid="([^"]+)"')
PAGES = {"overview", "settings"}
PLATFORMS = {"all", "tg", "hh"}

# Контейнеры, которые заполняет JS: до ответа сервера это пустые узлы
# нулевой высоты. Подсветить такой значит подсветить полоску в ноль
# пикселей, поэтому тур целится в статическую обёртку вокруг них.
JS_FILLED = {
    "hhLoginButtons", "resumeList", "presetBar", "kwList", "hhKwList",
    "exList", "hhExList", "channelEditList", "seleniumStepsList", "recentLog",
}


def _steps() -> list[tuple[str | None, str | None, str]]:
    block = STEPS_BLOCK.search(_tour_source())
    assert block, "массив TOUR_STEPS не найден — изменилась его форма?"
    parsed = []
    for page, target, platforms in STEP_ENTRY.findall(block.group(1)):
        unquote = lambda v: None if v == "null" else v.strip("'")  # noqa: E731
        parsed.append((unquote(page), unquote(target), platforms))
    return parsed


def test_the_scenario_parsed_at_all() -> None:
    """Непустота разбора. Без неё сломанное регулярное выражение давало бы
    зелёный прогон на пустом множестве шагов — и все проверки ниже
    превратились бы в проверки ничего."""
    steps = _steps()
    assert len(steps) >= 10, f"разобрано всего {len(steps)} шагов"
    assert sum(1 for _page, target, _p in steps if target) >= 8, (
        "почти ни у одного шага нет цели — разбор поля target сломан"
    )


def test_every_target_exists_in_the_markup() -> None:
    """Главный тест файла. Тур — копия знаний о вёрстке; без этой проверки
    переименование `id` оставляет тур подсвечивающим пустоту, и молча."""
    ids = set(ID_ATTR.findall(_index_source()))
    missing = [
        target for _page, target, _p in _steps()
        if target and target.lstrip("#") not in ids
    ]
    assert not missing, f"шаги ведут на элементы, которых нет в разметке: {missing}"


def test_the_existence_check_is_not_vacuous() -> None:
    """Обратная сторона: проверка обязана уметь падать."""
    ids = set(ID_ATTR.findall(_index_source()))
    assert ids, "из разметки не извлечён ни один id — сканер сломан"
    assert "safeModeRow" in ids, "сканер не видит id, который точно есть в разметке"
    assert "заведомо-отсутствующий-id" not in ids, "сканер находит то, чего нет"


def test_no_target_is_a_container_the_javascript_fills() -> None:
    """Решение из раздела 2 спецификации: целиться в статическую обёртку.
    `#hhLoginButtons` до ответа сервера — пустой div нулевой высоты."""
    offenders = [
        target for _page, target, _p in _steps()
        if target and target.lstrip("#") in JS_FILLED
    ]
    assert not offenders, (
        f"цели подсветки заполняются из JS и до ответа сервера пусты: {offenders}"
    )


def test_every_step_names_a_known_page() -> None:
    for page, target, platforms in _steps():
        assert page in PAGES or page is None, f"неизвестный экран: {page}"
        assert platforms in PLATFORMS, f"неизвестная площадка: {platforms}"
        if page is None:
            assert target is None, "шаг без экрана не может иметь цель на экране"


def test_the_safe_mode_step_exists_and_is_not_the_last() -> None:
    """Безопасный режим — развилка «складываю в очередь» против
    «откликаюсь за вас», и она единственная действует без подтверждения.
    После неё обязаны идти сохранение и запуск: закончить тур на настройке,
    которую некуда применить, значит не довести до работающего поиска."""
    steps = _steps()
    targets = [target for _page, target, _p in steps]
    assert "#safeModeRow" in targets, "в сценарии нет шага про безопасный режим"
    assert targets.index("#safeModeRow") < len(steps) - 1, (
        "безопасный режим — последний шаг тура"
    )


def test_both_save_buttons_are_in_the_scenario() -> None:
    """Критерии и настройки сохраняются раздельно, кнопки на разных
    экранах. Забыть вторую — самая дешёвая из возможных ошибок, и тур
    обязан показать обе."""
    targets = {target for _page, target, _p in _steps()}
    assert "#btnSaveSettings" in targets, "тур не показывает сохранение настроек"
    assert "#btnSaveCriteria" in targets, "тур не показывает сохранение критериев"


def test_the_tour_reaches_the_start_buttons() -> None:
    """D25: тур доводит до кнопок запуска — иначе он не доводит до поиска."""
    targets = {target for _page, target, _p in _steps()}
    assert "#workerBar" in targets, "тур не доходит до кнопок запуска воркеров"


@skip_without_node
def test_the_platform_filter_drops_the_other_platform() -> None:
    """D23: выбравшему только hh.ru не предлагается регистрировать
    приложение на my.telegram.org."""
    source = _tour_source()
    block = STEPS_BLOCK.search(source)
    assert block, "массив TOUR_STEPS не найден"
    script = "\n".join((
        block.group(0),
        _extract_function_source(source, "stepsFor"),
        """
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name); process.exitCode = 1; }
        }
        const both = stepsFor(['tg', 'hh']);
        const tg = stepsFor(['tg']);
        const hh = stepsFor(['hh']);
        check('обе площадки дают весь сценарий', both.length === TOUR_STEPS.length);
        check('только tg короче полного', tg.length < both.length);
        check('только hh короче полного', hh.length < both.length);
        check('в tg нет шагов hh', tg.every(s => s.platforms !== 'hh'));
        check('в hh нет шагов tg', hh.every(s => s.platforms !== 'tg'));
        const common = TOUR_STEPS.filter(s => s.platforms === 'all').length;
        for (const [name, list] of [['tg', tg], ['hh', hh], ['обе', both]]) {
          const got = list.filter(s => s.platforms === 'all').length;
          check(name + ': общие шаги на месте и не задвоены', got === common);
        }
        check('безопасный режим есть в любом выборе',
              [tg, hh, both].every(l => l.some(s => s.target === '#safeModeRow')));
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: FAILED — `массив TOUR_STEPS не найден`.

- [ ] **Шаг 3: Добавить `id` в `frontend/index.html`**

Одиннадцать точечных правок. Ни одна не меняет существующего поведения:
код обращается к внутренним полям (`#newKw`, `#kwList`), а не к контейнерам.

```
`#criteriaTg`:
  <details class="field-group">                 → <details class="field-group" id="groupChannels">
      (тот, где summary «Каналы»)
  <details class="field-group">                 → <details class="field-group" id="groupKeywords">
      (тот, где summary «Ключевые слова»)

`#criteriaHh`:
  <details class="field-group">                 → <details class="field-group" id="groupProfessions">
      (тот, где summary «Профессии»)
  <details class="field-group">                 → <details class="field-group" id="groupHhFilters">
      (тот, где summary «Фильтры поиска»)

«Обзор»:
  <div class="worker-bar">                      → <div class="worker-bar" id="workerBar">
  <button class="btn btn-primary" data-action="saveCriteria">Сохранить критерии</button>
        → <button class="btn btn-primary" id="btnSaveCriteria" data-action="saveCriteria">Сохранить критерии</button>

«Настройки»:
  <button class="btn btn-primary" data-action="saveGlobalSettings">Сохранить</button>
        → <button class="btn btn-primary" id="btnSaveSettings" data-action="saveGlobalSettings">Сохранить</button>

`#settingsCommon`:
  <div class="toggle-row">                      → <div class="toggle-row" id="safeModeRow">
      (тот, где «Безопасный режим»)

`#settingsTg`:
  <details class="field-group">                 → <details class="field-group" id="tgApiKeys">
      (тот, где summary «Ключи API»)
```

Блоки входа обёртки не имеют — их надо создать. В `#settingsTg` строки
«Вход в аккаунт», `#authStatus` и `#authForm` обернуть:

```html
        <div id="tgAuthBlock">
          <div class="field-label">Вход в аккаунт</div>
          <div id="authStatus" style="font-size:13px;color:var(--muted);margin-bottom:10px">Проверка...</div>
          <div id="authForm" style="display:none">
            ...без изменений...
          </div>
        </div>
```

В `#settingsHh` — так же, вокруг «Вход в аккаунт», `#hhLoginStatus`,
`#hhLoginButtons` и `#btnHhLoginCancel`:

```html
        <div id="hhAuthBlock">
          <div class="field-label">Вход в аккаунт</div>
          <div class="field-hint">Открывается настоящий Chrome — войдите в нём и вернитесь сюда</div>
          <div id="hhLoginStatus" style="font-size:13px;color:var(--muted);margin-bottom:10px"></div>
          <div id="hhLoginButtons" style="display:flex;gap:8px;flex-wrap:wrap"></div>
          <button class="btn btn-secondary" id="btnHhLoginCancel" style="margin-top:8px"
                  data-action="hhLoginCancel">Закрыть окно входа</button>
        </div>
```

- [ ] **Шаг 4: Добавить сценарий в `frontend/tour.js`**

Дописать после геометрии. Форма записи важна: `tests/test_frontend_tour.py`
разбирает поля `page`, `target` и `platforms` регулярным выражением и ждёт их
именно в этом порядке, первыми в объекте.

```javascript
// Сценарий фиксирован (D22): состояние настроек тур не читает и уже
// сделанные шаги не отмечает. Взамен он не зависит от формы ответов API и
// не разъедется с ними при следующем изменении настроек.
//
// Шаги «Настроек» идут раньше шагов «Обзора» не по смыслу, а чтобы тур
// переключал вкладку один раз, а не пять.
const TOUR_STEPS = [
  {
    page: null, target: null, platforms: 'all', choices: true,
    title: 'Настроим поиск',
    text: [
      'Пройдём по всему, что нужно заполнить, чтобы приложение начало находить вакансии. Это пара минут.',
      'Подсвеченное поле можно заполнять прямо во время тура — он не мешает вводить и нажимать.',
      'С чем работаем?',
    ],
  },
  {
    page: 'settings', target: '#tgApiKeys', platforms: 'tg',
    title: 'Ключи API Telegram',
    text: [
      'Приложение читает каналы вашим собственным аккаунтом, и Telegram требует для этого пару ключей. Они выдаются бесплатно и за минуту.',
      'Откройте ссылку внутри блока, войдите, нажмите Create application и скопируйте сюда api_id и api_hash. Ключи сохраняются своей кнопкой, внутри этого же блока.',
    ],
  },
  {
    page: 'settings', target: '#tgAuthBlock', platforms: 'tg',
    title: 'Вход в Telegram',
    text: [
      'Введите номер телефона, нажмите «Отправить код» и подтвердите кодом, который придёт в сам Telegram.',
      'Если на аккаунте включён облачный пароль, появится ещё одно поле.',
    ],
  },
  {
    page: 'settings', target: '#hhAuthBlock', platforms: 'hh',
    title: 'Вход в hh.ru',
    text: [
      'Нажмите кнопку входа — откроется настоящее окно Chrome. Войдите в нём как обычно и вернитесь сюда, чтобы сохранить сессию.',
      'Пароль от hh.ru приложение не видит и не хранит: оно работает с уже открытой сессией браузера.',
    ],
  },
  {
    page: 'settings', target: '#settingsResumes', platforms: 'all',
    title: 'Резюме',
    text: [
      'Загрузите файл резюме — он прикладывается к откликам. Хранится он в каталоге данных, а не в папке проекта: в резюме есть ФИО, телефон и почта.',
      'В безопасном режиме, о котором следующий шаг, ничего не отправляется — тогда файл можно и не загружать.',
    ],
  },
  {
    page: 'settings', target: '#safeModeRow', platforms: 'all',
    title: 'Безопасный режим — главная развилка',
    text: [
      'Включён: приложение ищет вакансии и складывает их на экран «Найдено», но ничего не отправляет. Откликаетесь вы сами.',
      'Выключен: приложение само пишет в Telegram и само нажимает «Откликнуться» на hh.ru — без подтверждения на каждую вакансию.',
      'Начните с включённого. Посмотрите несколько дней, что именно оно находит, и выключайте, только когда результаты перестанут удивлять.',
    ],
  },
  {
    page: 'settings', target: '#btnSaveSettings', platforms: 'all',
    title: 'Сохраните настройки',
    text: [
      'Всё с этого экрана сохраняется одной кнопкой. Ключи API — исключение, у них своя кнопка внутри блока Telegram.',
    ],
  },
  {
    page: 'overview', target: '#groupChannels', platforms: 'tg',
    title: 'Каналы Telegram',
    text: [
      'Добавьте каналы, которые приложение будет читать: @username или просто username.',
      'Enter добавляет значение в список. Уже добавленное можно исправить — щёлкните по нему и правьте на месте.',
    ],
  },
  {
    page: 'overview', target: '#groupKeywords', platforms: 'tg',
    title: 'Ключевые слова',
    text: [
      'Пост считается вакансией, если в нём встретилось хотя бы одно из этих слов.',
      'Кнопка «← добавить профессии с hh.ru» перенесёт сюда список из блока hh.ru. Она добавляет к вашему списку, а не заменяет его.',
    ],
  },
  {
    page: 'overview', target: '#groupProfessions', platforms: 'hh',
    title: 'Профессии на hh.ru',
    text: [
      'Поиск идёт по названию должности, а не по тексту вакансии. «Python» найдёт «Python-разработчика», но не вакансию, где Python упомянут среди требований.',
      'Такой же перенос работает и в обратную сторону — из ключевых слов Telegram.',
    ],
  },
  {
    page: 'overview', target: '#groupHhFilters', platforms: 'hh',
    title: 'Фильтры hh.ru',
    text: [
      'Опыт, зарплата от, период поиска, график и тип занятости. Зарплата 0 означает «не фильтровать».',
      'Регион — константа приложения и настройкой не является.',
    ],
  },
  {
    page: 'overview', target: '#btnSaveCriteria', platforms: 'all',
    title: 'Сохраните критерии — это вторая кнопка',
    text: [
      'Критерии принадлежат пресету и сохраняются отдельно от настроек: разные кнопки на разных экранах.',
      'Забыть эту — самая частая ошибка. Заполненные поля выглядят точно так же, как сохранённые.',
    ],
  },
  {
    page: 'overview', target: '#workerBar', platforms: 'all',
    title: 'Запуск',
    text: [
      'Теперь можно запускать. Каждая площадка запускается своей кнопкой и работает независимо от другой.',
      'Страницу можно обновлять и закрывать: воркеры живут в приложении, а не во вкладке браузера.',
    ],
  },
  {
    page: null, target: null, platforms: 'all',
    title: 'Готово',
    text: [
      'Найденное копится на экране «Найдено»: там ссылка на вакансию, кнопка «Откликнулся сам» и «Не подходит».',
      'Полоса вверху всегда показывает, работают ли воркеры и включён ли безопасный режим.',
      'Этот тур можно открыть снова — кнопкой «?» рядом с названием приложения.',
    ],
  },
];

function stepsFor(platforms) {
  return TOUR_STEPS.filter(
    step => step.platforms === 'all' || platforms.includes(step.platforms)
  );
}
```

- [ ] **Шаг 5: Убедиться, что тесты проходят**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: 16 passed (без node — 12 passed, 4 skipped).

- [ ] **Шаг 6: Полный прогон**

Запустить: `make test`
Ожидается: зелёный. Особое внимание к
`tests/test_frontend_screens.py::test_no_card_lives_outside_a_screen` — обёртки
`#tgAuthBlock` и `#hhAuthBlock` добавляют `div` внутрь карточек, и тест обязан
остаться зелёным. Если он покраснел — потерян закрывающий `</div>`.

- [ ] **Шаг 7: Коммит**

```bash
git add frontend/tour.js frontend/index.html tests/test_frontend_tour.py
git commit -m "$(cat <<'EOF'
feat(ui): сценарий обучения и цели подсветки

Четырнадцать шагов от развилки «Telegram / hh.ru / обе» до кнопок
запуска. Шаги «Настроек» идут первыми, чтобы тур переключал вкладку
один раз, а не пять.

Одиннадцать целей получили id, блоки входа — обёртки. Тур целится в
статическую обёртку, а не в #hhLoginButtons: тот до ответа сервера пуст
и подсветился бы полоской нулевой высоты.

Тест «каждая цель существует в разметке» — главный в наборе: без него
переименование id оставляет тур подсвечивающим пустоту, и молча.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
EOF
)"
```

---

### Задача 4: слой затемнения и показ шага

**Файлы:**
- Изменить: `frontend/index.html` (разметка `#tourLayer`)
- Изменить: `frontend/style.css` (стили слоя)
- Изменить: `frontend/tour.js` (`showStep`, `positionTour`, `renderTourCard`,
  `openAncestorDetails`, `placeRect`, `padRect`)
- Тест: `tests/test_frontend_tour.py`

**Интерфейсы:**
- Потребляет: `switchPage(page)` (задача 1), `maskRects`, `cardPosition`
  (задача 2), `TOUR_STEPS`, `stepsFor` (задача 3), `el`/`fill` из `app.js`.
- Отдаёт: `tourState` — `{ steps, index, platforms, active, node }`;
  `async function showStep(i, dir)` — показывает шаг `i`, а при отсутствующей
  цели уходит на следующий в сторону `dir`; `function positionTour()` —
  пересчитывает маски, рамку и карточку по текущему `tourState.node`.

- [ ] **Шаг 1: Написать падающие тесты**

Дописать в `tests/test_frontend_tour.py`:

```python
# ── Слой затемнения ───────────────────────────────────────────────────

TOUR_LAYER_IDS = (
    "tourLayer", "tourMaskTop", "tourMaskBottom", "tourMaskLeft", "tourMaskRight",
    "tourRing", "tourCard", "tourStepCounter", "tourCardTitle", "tourCardText",
    "tourCardChoices", "tourBtnSkip", "tourBtnBack", "tourBtnNext",
)


def test_the_layer_markup_is_complete() -> None:
    ids = set(ID_ATTR.findall(_index_source()))
    missing = [name for name in TOUR_LAYER_IDS if name not in ids]
    assert not missing, f"в разметке нет узлов слоя обучения: {missing}"


def test_the_dimming_blocks_clicks_and_the_hole_does_not() -> None:
    """D24 целиком держится на трёх правилах `pointer-events`.

    Слой накрывает весь экран, поэтому сам обязан быть прозрачным для
    курсора; клики ловят маски; рамка вокруг дырки — не ловит, иначе она
    съела бы клик по краю подсвеченной кнопки.
    """
    css = (FRONTEND_DIR / "style.css").read_text(encoding="utf-8")

    def rule(selector: str) -> str:
        match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
        assert match, f"в style.css нет правила для {selector}"
        return match.group(1)

    assert "pointer-events: none" in rule("#tourLayer"), (
        "слой обучения ловит клики сам — дырка не будет нажиматься"
    )
    assert "pointer-events: auto" in rule(".tour-mask"), (
        "затемнение пропускает клики — можно уйти на другую страницу из-под тура"
    )
    assert "pointer-events: none" in rule(".tour-ring"), (
        "рамка вокруг дырки ловит клики по краю подсвеченного элемента"
    )
    assert "pointer-events: auto" in rule(".tour-card"), (
        "кнопки карточки не нажимаются"
    )


def test_the_tour_never_builds_markup_from_strings() -> None:
    """Тот же инвариант, что держит app.js: содержимое приходит из
    Telegram и hh.ru, и путь «строка → разметка» не должен существовать
    вовсе, даже там, где сегодня подставляются только свои тексты."""
    source = _tour_source()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in source, f"tour.js собирает разметку через {forbidden}"
    assert not re.search(r"""\son[a-z]+\s*=\s*["']""", source), (
        "tour.js проставляет атрибут-обработчик — это вернёт 'unsafe-inline' в CSP"
    )


def test_the_tour_does_not_duplicate_page_switching() -> None:
    """Вторая копия логики вкладок разойдётся с первой при первой правке."""
    source = _tour_source()
    assert "switchPage" in source, "тур не переключает страницы"
    assert ".nav-item" not in source, "tour.js сам лезет в навигацию"
    assert "classList.add('active')" not in source, (
        "tour.js сам ставит active — это копия switchPage"
    )


def test_the_tour_opens_the_accordions_it_points_at() -> None:
    """Пять из одиннадцати целей — свёрнутые <details>. У скрытого
    содержимого getBoundingClientRect даёт нули, и подсветка встала бы в
    угол экрана нулевого размера."""
    source = _tour_source()
    assert "details" in source.lower(), "тур не раскрывает аккордеоны"
    assert re.search(r"\.open\s*=\s*true", source), (
        "ни один <details> не раскрывается принудительно"
    )


def test_the_position_is_recomputed_on_scroll_and_resize() -> None:
    """Раскрытие аккордеона и прокрутка сдвигают цель. Без пересчёта
    подсветка остаётся там, где элемент был."""
    source = _tour_source()
    assert "'scroll'" in source, "позиция не пересчитывается при прокрутке"
    assert "'resize'" in source, "позиция не пересчитывается при смене размера окна"
    assert "positionTour" in source
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: FAILED — нет узлов слоя, нет правил в CSS.

- [ ] **Шаг 3: Добавить разметку слоя**

В `frontend/index.html`, рядом с `#aboutModal` (то есть вне всех `.page`),
перед тегами `<script>`:

```html
<!-- ── Пошаговое обучение ──────────────────────────────────────────── -->
<!-- Четыре маски вокруг дырки, а не одна тень: box-shadow затемняет, но
     дырки в попадании курсора не делает, и подсвеченную кнопку было бы
     нельзя нажать. Кнопки развилки лежат здесь, а не строятся кодом:
     сканы data-action смотрят в index.html и app.js, и построенная в
     tour.js кнопка не попала бы ни в один из них. -->
<div id="tourLayer" hidden>
  <div class="tour-mask" id="tourMaskTop"></div>
  <div class="tour-mask" id="tourMaskBottom"></div>
  <div class="tour-mask" id="tourMaskLeft"></div>
  <div class="tour-mask" id="tourMaskRight"></div>
  <div class="tour-ring" id="tourRing"></div>
  <div class="tour-card" id="tourCard" role="dialog" aria-modal="true"
       aria-labelledby="tourCardTitle">
    <div class="tour-step" id="tourStepCounter"></div>
    <div class="tour-card-title" id="tourCardTitle"></div>
    <div class="tour-card-text" id="tourCardText"></div>
    <div class="tour-card-choices" id="tourCardChoices" hidden>
      <button class="btn btn-secondary" data-action="tourChoose" data-arg="tg">Только Telegram</button>
      <button class="btn btn-secondary" data-action="tourChoose" data-arg="hh">Только hh.ru</button>
      <button class="btn btn-primary" data-action="tourChoose" data-arg="both">Обе площадки</button>
    </div>
    <div class="tour-card-buttons">
      <button class="tour-skip" id="tourBtnSkip" data-action="tourSkip">Пропустить</button>
      <button class="btn btn-secondary" id="tourBtnBack" data-action="tourBack">Назад</button>
      <button class="btn btn-primary" id="tourBtnNext" data-action="tourNext">Далее</button>
    </div>
  </div>
</div>
```

- [ ] **Шаг 4: Добавить стили**

В конец `frontend/style.css`:

```css
/* ── Пошаговое обучение ─────────────────────────────────────────────── */
/* Три правила pointer-events держат всё поведение слоя: сам он прозрачен
   для курсора (иначе накрыл бы и дырку), клики ловят маски, а рамка вокруг
   дырки — не ловит, иначе съела бы клик по краю подсвеченной кнопки. */
#tourLayer { position: fixed; inset: 0; z-index: 2000; pointer-events: none; }
.tour-mask { position: fixed; background: rgba(0,0,0,.62); pointer-events: auto; }
.tour-ring { position: fixed; pointer-events: none; border: 2px solid var(--accent);
             border-radius: var(--radius-sm); box-shadow: 0 0 0 3px rgba(255,255,255,.28); }
.tour-card { position: fixed; pointer-events: auto; width: 340px;
             max-width: calc(100vw - 32px); background: var(--card-bg);
             border-radius: var(--radius); padding: 18px 20px;
             box-shadow: 0 12px 44px rgba(0,0,0,.3); }
.tour-step { font-size: 11px; font-weight: 600; letter-spacing: .05em;
             text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
.tour-card-title { font-size: 15px; font-weight: 700; color: var(--text); margin-bottom: 8px; }
.tour-card-text p { font-size: 12.5px; line-height: 1.55; color: var(--muted); margin: 0 0 8px; }
.tour-card-text p:last-child { margin-bottom: 0; }
.tour-card-choices { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
.tour-card-buttons { display: flex; gap: 8px; align-items: center; margin-top: 14px; }
.tour-skip { margin-right: auto; background: none; border: none; cursor: pointer;
             font-size: 12px; color: var(--muted); padding: 6px 0; font-family: inherit; }
.tour-skip:hover { color: var(--text); }
```

- [ ] **Шаг 5: Реализовать показ шага**

Дописать в `frontend/tour.js` после `stepsFor`:

```javascript
// ── Показ шага ────────────────────────────────────────────────────────

const TOUR_MASK_IDS = {
  top: 'tourMaskTop',
  bottom: 'tourMaskBottom',
  left: 'tourMaskLeft',
  right: 'tourMaskRight',
};

const tourState = { steps: TOUR_STEPS, index: 0, platforms: ['tg', 'hh'], active: false, node: null };

function padRect(rect, padding) {
  return {
    x: rect.left - padding,
    y: rect.top - padding,
    width: rect.width + padding * 2,
    height: rect.height + padding * 2,
  };
}

function placeRect(node, rect) {
  node.style.left = rect.x + 'px';
  node.style.top = rect.y + 'px';
  node.style.width = rect.width + 'px';
  node.style.height = rect.height + 'px';
}

// Цель может лежать в свёрнутом <details> — а то и в двух вложенных.
// У скрытого содержимого getBoundingClientRect возвращает нули, и подсветка
// встала бы в левый верхний угол размером в ноль. Раскрытое туром обратно не
// сворачивается: пользователь только что это заполнил.
function openAncestorDetails(node) {
  for (let cur = node; cur; cur = cur.parentElement) {
    if (cur.tagName === 'DETAILS') cur.open = true;
  }
}

function renderTourCard(step, index) {
  const last = index === tourState.steps.length - 1;
  document.getElementById('tourStepCounter').textContent =
    `Шаг ${index + 1} из ${tourState.steps.length}`;
  document.getElementById('tourCardTitle').textContent = step.title;
  fill(
    document.getElementById('tourCardText'),
    step.text.map(line => el('p', { text: line }))
  );
  document.getElementById('tourCardChoices').hidden = !step.choices;
  document.getElementById('tourBtnBack').hidden = index === 0;
  const next = document.getElementById('tourBtnNext');
  next.hidden = Boolean(step.choices);
  next.textContent = last ? 'Готово' : 'Далее';
}

function positionTour() {
  if (!tourState.active) return;
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const hole = tourState.node
    ? padRect(tourState.node.getBoundingClientRect(), TOUR_PADDING)
    : null;
  const masks = maskRects(hole, viewport);
  for (const [side, id] of Object.entries(TOUR_MASK_IDS)) {
    placeRect(document.getElementById(id), masks[side]);
  }
  const ring = document.getElementById('tourRing');
  ring.hidden = !hole;
  if (hole) placeRect(ring, hole);
  const card = document.getElementById('tourCard');
  const position = cardPosition(
    hole,
    { width: card.offsetWidth, height: card.offsetHeight },
    viewport
  );
  card.style.left = position.x + 'px';
  card.style.top = position.y + 'px';
}

async function showStep(index, dir) {
  const step = tourState.steps[index];
  if (!step) { endTour(); return; }
  tourState.index = index;
  if (step.page) await switchPage(step.page);
  let node = null;
  if (step.target) {
    node = document.querySelector(step.target);
    if (!node) {
      // D27: подсветить пустоту хуже, чем пропустить шаг. Уходим в ту же
      // сторону, откуда пришли: пропуск всегда вперёд сделал бы кнопку
      // «Назад» через такой шаг возвратом туда, откуда только что ушли.
      await tourMove(dir);
      return;
    }
    openAncestorDetails(node);
    node.scrollIntoView({ block: 'center' });
  }
  tourState.node = node;
  renderTourCard(step, index);
  positionTour();
}

// Захват, а не всплытие: прокручивается внутренний контейнер `.main`, и
// его событие до window не всплывает.
window.addEventListener('scroll', positionTour, true);
window.addEventListener('resize', positionTour);
```

- [ ] **Шаг 6: Убедиться, что тесты проходят**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: 22 passed (без node — 18 passed, 4 skipped) — **весь файл зелёный**.

Это стоит понимать правильно. `showStep` уже ссылается на `tourMove` и
`endTour`, которых ещё нет: в браузере первый же показ шага упал бы с
`ReferenceError`. Прогон этого не видит, потому что ни один тест не исполняет
`tour.js` целиком — текстовые читают исходник, а node-тесты подставляют по
одной функции. Дыру закрывает задача 5; до неё показать шаг всё равно нечем,
действий в `ACTIONS` ещё нет и кнопка вызова не добавлена.

Вывод на будущее: зелёный прогон здесь не означает работающий тур, и
единственная проверка, которая это увидит, — задача 6.

- [ ] **Шаг 7: Полный прогон**

Запустить: `make test`
Ожидается: зелёный. `test_no_card_lives_outside_a_screen` видит новый блок вне
страниц — убедиться, что `#tourLayer` не имеет класса `card`.

- [ ] **Шаг 8: Коммит**

```bash
git add frontend/index.html frontend/style.css frontend/tour.js tests/test_frontend_tour.py
git commit -m "$(cat <<'EOF'
feat(ui): слой затемнения и показ шага обучения

Слой прозрачен для курсора, клики ловят четыре маски, рамка вокруг дырки
не ловит — иначе она съедала бы клик по краю подсвеченной кнопки. Три
правила pointer-events и держат всё поведение.

Перед замером раскрываются все родительские <details>: у скрытого
содержимого getBoundingClientRect даёт нули, и подсветка встала бы в
угол экрана нулевого размера. Обратно тур их не сворачивает.

Прокрутка слушается в фазе захвата: скроллится внутренний .main, и до
window его событие не всплывает.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
EOF
)"
```

---

### Задача 5: управление туром — запуск, навигация, выход

**Файлы:**
- Изменить: `frontend/tour.js` (`startTour`, `tourChoose`, `tourNext`,
  `tourBack`, `tourSkip`, `tourMove`, `endTour`, `maybeAutoStartTour`,
  клавиатура)
- Изменить: `frontend/app.js` (`ACTIONS`, вызов из `init()`)
- Изменить: `frontend/index.html` (кнопка «?» в шапке)
- Тест: `tests/test_frontend_tour.py`

**Интерфейсы:**
- Потребляет: `showStep(index, dir)`, `tourState`, `stepsFor` (задачи 3–4).
- Отдаёт: `startTour()`, `tourChoose(arg)`, `tourNext()`, `tourBack()`,
  `tourSkip()`, `maybeAutoStartTour()`. Первые пять попадают в `ACTIONS`;
  последняя вызывается из `init()` и действием не является.

- [ ] **Шаг 1: Написать падающие тесты**

Дописать в `tests/test_frontend_tour.py`:

```python
# ── Управление ────────────────────────────────────────────────────────

TOUR_ACTIONS = ("startTour", "tourChoose", "tourNext", "tourBack", "tourSkip")


def test_every_tour_action_is_registered() -> None:
    """Незарегистрированное действие — мёртвая кнопка: runAction берёт
    ACTIONS[name], получает undefined и молча возвращается. Ни ошибки в
    консоли, ни следа."""
    actions = re.search(r"const ACTIONS = \{(.*?)\n\};", _app_source(), re.DOTALL)
    assert actions, "ACTIONS не найдена в app.js"
    registered = set(re.findall(r"^\s*([A-Za-z_$][\w$]*),", actions.group(1), re.MULTILINE))
    missing = [name for name in TOUR_ACTIONS if name not in registered]
    assert not missing, f"действия тура не зарегистрированы в ACTIONS: {missing}"


def test_the_tour_can_be_reopened_from_the_header() -> None:
    """Тур показывается один раз сам; дальше он нужен по требованию."""
    source = _index_source()
    assert 'data-action="startTour"' in source, (
        "тур нельзя открыть повторно — кнопки вызова нет в разметке"
    )


def test_the_tour_is_started_from_init_not_from_its_own_listener() -> None:
    """init() грузит пресеты, критерии и настройки. Тур, запущенный
    раньше, переключил бы вкладку и стал мерить элементы на странице,
    которую в этот момент ещё перерисовывают. Своего DOMContentLoaded у
    тура нет ещё и потому, что tour.js подключён раньше app.js — его
    обработчик сработал бы до init() гарантированно."""
    app = _app_source()
    init = _extract_function_source(app, "init")
    assert "maybeAutoStartTour" in init, "init() не запускает обучение"
    assert "DOMContentLoaded" not in _tour_source(), (
        "tour.js вешает свой DOMContentLoaded — он сработает до init()"
    )


def test_the_seen_flag_is_written_on_every_exit() -> None:
    """Флаг ставится и на «Пропустить», и на Escape, и на последнем шаге:
    иначе тур возвращался бы при каждой перезагрузке к человеку, который
    его уже закрыл. Поэтому запись живёт в endTour, через который проходят
    все три выхода."""
    source = _tour_source()
    end = _extract_function_source(source, "endTour")
    assert "localStorage.setItem" in end, "endTour не запоминает, что тур показан"
    assert source.count("localStorage.setItem") == 1, (
        "флаг пишется не только в endTour — появился второй выход мимо него"
    )
    assert "try" in end, (
        "обращение к localStorage не защищено: в приватном режиме оно бросает, "
        "и тур упал бы на кнопке «Пропустить»"
    )


def test_enter_is_left_to_the_lists() -> None:
    """Enter уже занят: data-enter-action добавляет значение в список, и
    подсвеченное поле ввода обязано продолжать работать через дырку."""
    source = _tour_source()
    assert "'Enter'" not in source, (
        "тур перехватывает Enter — ввод в подсвеченном поле перестанет добавлять значения"
    )
    assert "'Escape'" in source, "тур не закрывается по Escape"


def test_the_arrows_are_ignored_inside_fields() -> None:
    """В поле ввода стрелки двигают курсор, а не листают тур."""
    source = _tour_source()
    assert "ArrowRight" in source and "ArrowLeft" in source, "стрелки не листают тур"
    assert re.search(r"INPUT|TEXTAREA|tagName", source), (
        "стрелки перехватываются и внутри полей ввода"
    )


@skip_without_node
def test_a_missing_target_is_skipped_in_the_direction_of_travel() -> None:
    """D27 в обе стороны. Если пропуск всегда вёл бы вперёд, «Назад» через
    отсутствующий шаг возвращал бы туда, откуда только что ушли, и выйти
    назад стало бы невозможно.

    Проверяется на подделке DOM: третий шаг «не найден», и обход идёт
    сначала вперёд, потом назад.
    """
    source = _tour_source()
    script = "\n".join((
        "const visited = [];",
        "const missing = '#нет-такого';",
        """
        globalThis.window = { addEventListener() {}, innerWidth: 1280, innerHeight: 800 };
        globalThis.document = {
          querySelector: sel => (sel === missing ? null : {
            id: sel,
            scrollIntoView() {},
            getBoundingClientRect: () => ({ left: 10, top: 10, width: 100, height: 40 }),
          }),
          getElementById: () => ({ style: {}, hidden: false, textContent: '', offsetWidth: 340, offsetHeight: 260 }),
        };
        globalThis.switchPage = async () => {};
        globalThis.el = () => ({});
        globalThis.fill = () => {};
        globalThis.localStorage = { setItem() {}, getItem: () => null };
        """,
        _extract_function_source(source, "clampRect"),
        _extract_function_source(source, "maskRects"),
        _extract_function_source(source, "cardPosition"),
        "const TOUR_PADDING = 6; const TOUR_GAP = 12;",
        _extract_function_source(source, "padRect"),
        _extract_function_source(source, "placeRect"),
        _extract_function_source(source, "openAncestorDetails"),
        _extract_function_source(source, "positionTour"),
        _extract_function_source(source, "showStep"),
        _extract_function_source(source, "tourMove"),
        _extract_function_source(source, "endTour"),
        """
        const tourState = {
          steps: [
            { page: null, target: null, platforms: 'all', title: 'a', text: [] },
            { page: null, target: '#есть', platforms: 'all', title: 'b', text: [] },
            { page: null, target: missing, platforms: 'all', title: 'c', text: [] },
            { page: null, target: '#тоже-есть', platforms: 'all', title: 'd', text: [] },
          ],
          index: 0, platforms: ['tg'], active: true, node: null,
        };
        globalThis.tourState = tourState;
        globalThis.renderTourCard = (step) => visited.push(step.title);
        function check(name, cond) {
          if (!cond) { console.error('FAIL:', name, visited.join(',')); process.exitCode = 1; }
        }
        (async () => {
          await showStep(1, 1);
          await tourMove(1);
          check('вперёд отсутствующий шаг пропущен', tourState.index === 3);
          visited.length = 0;
          await tourMove(-1);
          check('назад отсутствующий шаг пропущен назад, а не вперёд', tourState.index === 1);
        })();
        """,
    ))
    result = _run_node(script)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: FAILED — действия не зарегистрированы, кнопки нет, `endTour` нет.

- [ ] **Шаг 3: Реализовать управление**

Дописать в `frontend/tour.js` после `showStep`, до подписки на `scroll`:

```javascript
// ── Управление ────────────────────────────────────────────────────────

const TOUR_SEEN_KEY = 'jobmonitor.tour.seen';

async function startTour() {
  tourState.platforms = ['tg', 'hh'];
  tourState.steps = stepsFor(tourState.platforms);
  tourState.active = true;
  tourState.node = null;
  document.getElementById('tourLayer').hidden = false;
  await showStep(0, 1);
}

// Развилка D23. Пересборка списка не сдвигает нулевой шаг — он общий, —
// поэтому дальше можно просто идти вперёд.
async function tourChoose(arg) {
  tourState.platforms = arg === 'both' ? ['tg', 'hh'] : [arg];
  tourState.steps = stepsFor(tourState.platforms);
  await tourMove(1);
}

async function tourMove(dir) {
  const next = tourState.index + dir;
  if (next < 0) return;
  if (next >= tourState.steps.length) { endTour(); return; }
  await showStep(next, dir);
}

async function tourNext() { await tourMove(1); }
async function tourBack() { await tourMove(-1); }
function tourSkip() { endTour(); }

function endTour() {
  tourState.active = false;
  tourState.node = null;
  const layer = document.getElementById('tourLayer');
  if (layer) layer.hidden = true;
  // В приватном окне обращение к localStorage бросает, и тур падал бы на
  // кнопке «Пропустить» — то есть на единственном способе от него уйти.
  try {
    localStorage.setItem(TOUR_SEEN_KEY, '1');
  } catch (e) {
    /* тур просто покажется ещё раз */
  }
}

function maybeAutoStartTour() {
  let seen = null;
  try {
    seen = localStorage.getItem(TOUR_SEEN_KEY);
  } catch (e) {
    seen = '1';   // не смогли прочитать — не навязываемся
  }
  if (!seen) startTour();
}

// Enter тур не трогает: он уже занят data-enter-action, который добавляет
// значение в список, и подсвеченное поле ввода обязано продолжать работать
// через дырку. Стрелки внутри полей тоже не перехватываются — там они
// двигают курсор.
document.addEventListener('keydown', event => {
  if (!tourState.active) return;
  if (event.key === 'Escape') { endTour(); return; }
  const tag = event.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (event.key === 'ArrowRight') tourNext();
  if (event.key === 'ArrowLeft') tourBack();
});
```

- [ ] **Шаг 4: Зарегистрировать действия и вызов из `init()`**

В `frontend/app.js`, в литерал `ACTIONS` — пять имён (алфавит там не
соблюдается, порядок группировки по смыслу; добавить в конец):

```javascript
  foundReopen,
  startTour,
  tourChoose,
  tourNext,
  tourBack,
  tourSkip,
};
```

Последней строкой `init()`, после `pollStatus();`:

```javascript
  // Последней строкой, а не из своего DOMContentLoaded: тур переключает
  // вкладку и меряет элементы, а до этой точки пресеты, критерии и
  // настройки ещё грузятся.
  maybeAutoStartTour();
```

- [ ] **Шаг 5: Добавить кнопку вызова**

В `frontend/index.html`, в `.sidebar-logo`, заменить одиночную кнопку на пару:

```html
    <div style="display:flex;gap:6px">
      <button class="btn-about" data-action="startTour" title="Обучение">?</button>
      <button class="btn-about" data-action="showAbout" title="О приложении">ⓘ</button>
    </div>
```

- [ ] **Шаг 6: Убедиться, что тесты проходят**

Запустить: `.venv/bin/pytest tests/test_frontend_tour.py -q`
Ожидается: 29 passed (без node — 24 passed, 5 skipped).

- [ ] **Шаг 7: Полный прогон**

Запустить: `make test`
Ожидается: зелёный. Особое внимание к
`tests/test_frontend_events.py::test_every_handler_is_reachable` — он падает,
если действие есть в `ACTIONS`, но ни одна кнопка его не вызывает. Все пять
имён обязаны быть в разметке `index.html`.

- [ ] **Шаг 8: Коммит**

```bash
git add frontend/tour.js frontend/app.js frontend/index.html tests/test_frontend_tour.py
git commit -m "$(cat <<'EOF'
feat(ui): запуск, навигация и выход из обучения

Тур показывается один раз сам — из конца init(), а не из своего
DOMContentLoaded: tour.js подключён раньше app.js, и собственный
обработчик сработал бы до загрузки пресетов и настроек. Повторно
открывается кнопкой «?» в шапке.

Отсутствующий шаг пропускается в сторону движения. Пропуск всегда вперёд
превратил бы «Назад» через такой шаг в возврат туда, откуда только что
ушли, и выйти назад стало бы невозможно.

Enter не перехватывается: он занят data-enter-action, добавляющим
значение в список, и подсвеченное поле должно работать через дырку.
Обращения к localStorage обёрнуты в try — в приватном окне оно бросает,
и тур падал бы на «Пропустить», единственном способе от него уйти.

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
EOF
)"
```

---

### Задача 6: проверка в браузере и документация

Три из четырёх дефектов прошлого подпроекта нашлись только при реальном
открытии страницы, а не рассуждением по коду. Тур — подсистема, которую тесты
проверяют текстом исходника, и увидеть её можно единственным способом.

**Файлы:**
- Изменить: `структура.txt`, `README.md`
- Изменить: `frontend/tour.js` или `frontend/style.css` по итогам осмотра

- [ ] **Шаг 1: Запустить приложение**

```bash
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Шаг 2: Пройти тур целиком, трижды**

Открыть `http://127.0.0.1:8000/` с очисткой кэша (`Cmd+Shift+R`). Проверить по
списку — каждый пункт отвечает конкретному решению спецификации:

1. Тур открылся сам (`localStorage` чист) — D26.
2. Выбор «Только hh.ru» убирает шаги Telegram, счётчик показывает меньшее
   число — D23.
3. Подсветка попадает точно в элемент, а не рядом.
4. Шаг с `#tgApiKeys` раскрыл аккордеон, и подсвечен раскрытый блок, а не
   полоска заголовка — раздел 8.
5. В подсвеченное поле можно печатать, подсвеченную кнопку можно нажать — D24.
6. Клик по затемнению и по пунктам навигации ничего не делает — D24.
7. Прокрутка колесом: подсветка едет вместе с элементом — раздел 7.
8. Карточка не накрывает подсвеченный элемент ни на одном шаге — раздел 7.
9. Escape закрывает; перезагрузка страницы не открывает тур снова — раздел 9.
10. Кнопка «?» открывает тур заново с нулевого шага — раздел 9.

- [ ] **Шаг 3: Исправить найденное**

Каждое расхождение — отдельный коммит с описанием того, что именно разошлось с
ожиданием. Если правка касается поведения, у неё сначала тест.

- [ ] **Шаг 4: Обновить описания дерева**

В `структура.txt`:

```
├── frontend/
│   ├── index.html              только разметка
│   ├── style.css               все стили
│   ├── tour.js                 пошаговое обучение: сценарий и затемнение
│   └── app.js                  вся логика
```

В `README.md`, в блоке структуры проекта, той же строкой:

```
│   ├── style.css               # Стили (фирменные цвета TG/HH)
│   ├── tour.js                 # Пошаговое обучение по интерфейсу
│   └── app.js                  # Вся логика фронтенда
```

- [ ] **Шаг 5: Полный прогон**

Запустить: `make test`
Ожидается: зелёный.
`tests/test_docs_structure.py` сверяет документы с деревом по именам `*.py` —
`tour.js` под его проверку не попадает, но расхождение описаний с деревом всё
равно дефект.

- [ ] **Шаг 6: Коммит**

```bash
git add структура.txt README.md
git commit -m "$(cat <<'EOF'
docs: frontend/tour.js в описаниях дерева проекта

Claude-Session: https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB
EOF
)"
```

- [ ] **Шаг 7: Пуш и пулл-реквест**

```bash
git push -u origin feature/onboarding-tour
gh pr create --base master --title "Пошаговое обучение по интерфейсу" --body "..."
```

Описание пулл-реквеста заканчивается строкой
`https://claude.ai/code/session_01N275pWwfa7TZptJcAdVnBB`.

---

## Самопроверка плана

**Покрытие спецификации.** Разделы 1–3 (зачем, находки, решения) — обоснование,
кода не требуют; D22 и D23 реализованы задачей 3, D24 — задачей 4, D25 — шагом
12 сценария, D26 и D27 — задачей 5, D28 — задачей 2. Раздел 4 (файлы) — задачи
2 и 6. Раздел 5 (слой) — задача 4. Раздел 6 (сценарий) — задача 3. Раздел 7
(геометрия) — задача 2. Раздел 8 (показ шага) — задача 4. Раздел 9 (запуск и
выход) — задача 5. Раздел 10 (правки существующего кода): `switchPage` —
задача 1, одиннадцать `id` — задача 3, `ACTIONS` — задача 5. Раздел 11 (тесты)
— тринадцать пунктов распределены по задачам 1–5. Раздел 12 (вне области)
кода не требует. Раздел 13 (ограничения) вынесен в шапку плана.

**Согласованность имён.** `switchPage(page)` (задача 1) вызывается в `showStep`
(задача 4). `maskRects`/`cardPosition`/`clampRect` (задача 2) — в
`positionTour` (задача 4). `TOUR_STEPS`/`stepsFor` (задача 3) — в `startTour` и
`tourChoose` (задача 5). `tourState` заводится в задаче 4 и используется в
задаче 5. `TOUR_PADDING` и `TOUR_GAP` объявлены в задаче 2, используются в
задачах 2 и 4. `showStep(index, dir)` и `tourMove(dir)` вызывают друг друга —
обе объявлениями функций, взаимная рекурсия разрешается подъёмом.

**Известная особенность порядка.** Задача 4 оставляет `tour.js` со ссылками на
`tourMove` и `endTour`, которых ещё нет. Прогон этого не замечает: ни один тест
не исполняет файл целиком. Шаг 6 задачи 4 называет это прямо, чтобы зелёный
прогон в середине работы не был прочитан как «тур работает».

**Чего план заведомо не проверяет.** Совпадение подсветки с элементом,
читаемость карточки и то, что клик действительно проходит в дырку, проверяются
только задачей 6, руками, в браузере. Тесты здесь держат связи (шаг → элемент,
действие → обработчик) и чистую геометрию; попадание пикселей они не видят.
Три из четырёх дефектов прошлого подпроекта нашлись именно так.
