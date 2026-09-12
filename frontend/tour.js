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
  // Без Math.max: обе границы обрезаны одинаково и в одном порядке, так что
  // x2 >= x1 по построению. Обёртка `Math.max(0, x2 - x1)` тут стояла и была
  // мёртвой — мутация, убиравшая её, не роняла ни одного теста, потому что
  // ронять было нечего.
  return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
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

// ── Показ шага ────────────────────────────────────────────────────────

const TOUR_MASK_IDS = {
  top: 'tourMaskTop',
  bottom: 'tourMaskBottom',
  left: 'tourMaskLeft',
  right: 'tourMaskRight',
};

const tourState = {
  steps: TOUR_STEPS,
  index: 0,
  platforms: ['tg', 'hh'],
  active: false,
  node: null,
};

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

// Захват, а не всплытие: прокручивается внутренний контейнер `.main`, и
// его событие до window не всплывает.
window.addEventListener('scroll', positionTour, true);
window.addEventListener('resize', positionTour);
