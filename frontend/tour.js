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
