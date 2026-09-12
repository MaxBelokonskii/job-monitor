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
