/* Гравюрная графика в стиле фирменного стакана: тонкая линия, штриховка,
   монохром. Все элементы рисуются currentColor, поэтому темы работают сами. */

/** Пальма. Ставится через transform на месте вызова. */
function Palm({ flip = false }: { flip?: boolean }) {
  return (
    <g transform={flip ? "scale(-1,1)" : undefined} fill="none" strokeLinecap="round">
      {/* ствол с кольцами — растёт вверх от точки установки */}
      <path d="M0 0 C -6 -60, 2 -114, 16 -166" strokeWidth="3.4" />
      <path d="M0 0 C 2 -60, 10 -114, 22 -166" strokeWidth="1.2" opacity=".55" />
      {[-30, -58, -84, -110, -136].map((y, i) => (
        <path key={y} d={`M${-3 + i * 2.6} ${y} q6 -3 11 0`} strokeWidth="1" opacity=".5" />
      ))}

      {/* листья */}
      {[
        "M18 -170 C -18 -190, -52 -186, -74 -166 C -46 -180, -22 -178, 16 -164",
        "M18 -170 C -8 -206, -40 -218, -70 -216 C -38 -206, -12 -192, 16 -166",
        "M18 -170 C 12 -206, 30 -232, 60 -242 C 36 -222, 26 -198, 20 -168",
        "M18 -170 C 44 -188, 74 -188, 96 -172 C 68 -182, 42 -178, 20 -166",
        "M18 -170 C 44 -166, 66 -150, 76 -128 C 58 -148, 38 -160, 20 -164",
      ].map((d, i) => (
        <g key={i}>
          <path d={d} strokeWidth="2" />
          <path d={d} strokeWidth="5" opacity=".1" />
        </g>
      ))}
      {/* кокосы */}
      <circle cx="11" cy="-158" r="4.5" strokeWidth="1.4" />
      <circle cx="23" cy="-153" r="4" strokeWidth="1.4" />
    </g>
  );
}

/** Гирлянда лампочек, как над террасой на референсе. */
function Garland({ width = 300 }: { width?: number }) {
  const count = Math.max(5, Math.round(width / 72)); // лампочки через равные промежутки
  const bulbs = Array.from({ length: count }, (_, i) => {
    const t = (i + 1) / (count + 1);
    const x = t * width;
    const y = Math.sin(Math.PI * t) * 26; // провисание троса
    return { x, y };
  });
  return (
    <g fill="none" strokeLinecap="round">
      <path d={`M0 0 Q ${width / 2} 52, ${width} 0`} strokeWidth="1.4" />
      {bulbs.map((b, i) => (
        <g key={i}>
          <path d={`M${b.x} ${b.y} v7`} strokeWidth="1.2" />
          <circle
            className="bulb"
            cx={b.x}
            cy={b.y + 11}
            r="4"
            strokeWidth="1.2"
            /* вразнобой, чтобы огоньки мерцали не в такт */
            style={{ animationDelay: `${(i % 5) * 0.45 + (i % 3) * 0.2}s` }}
          />
        </g>
      ))}
    </g>
  );
}

/** Доска для сёрфинга, воткнутая в песок. */
function Surfboard({ stripe = true }: { stripe?: boolean }) {
  return (
    <g fill="none" strokeLinejoin="round" strokeLinecap="round">
      <path d="M0 0 C -13 -28, -13 -70, 0 -98 C 13 -70, 13 -28, 0 0 Z" strokeWidth="1.8" />
      <path d="M0 -10 V -88" strokeWidth="1" opacity=".5" />
      {stripe && (
        <>
          <path d="M-9 -44 H9" strokeWidth="1" opacity=".5" />
          <path d="M-8 -56 H8" strokeWidth="1" opacity=".5" />
        </>
      )}
    </g>
  );
}

/** Сёрфер на гребне волны. */
function Surfer() {
  return (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      {/* волна с завитком */}
      <path d="M-62 28 C -44 -6, -8 -28, 26 -16 C 4 -22, -24 -8, -38 28" strokeWidth="1.8" />
      <path d="M26 -16 C 36 -7, 36 7, 25 15" strokeWidth="1.4" opacity=".75" />
      <path d="M-52 30 q14 -6 26 0" strokeWidth="1.1" opacity=".55" />
      {/* брызги с гребня */}
      <path d="M18 -24 l6 -9 M29 -21 l10 -5 M9 -27 l2 -10" strokeWidth="1" opacity=".6" />
      {/* доска */}
      <path d="M-27 18 C -14 11, 5 9, 17 13 C 4 20, -15 22, -27 18 Z" strokeWidth="1.6" />
      {/* фигура */}
      <path d="M-6 12 C -7 4, -4 0, -2 -4" strokeWidth="1.8" />
      <circle cx="-1" cy="-9" r="3.6" strokeWidth="1.4" />
      <path d="M-3 -2 l-12 -5" strokeWidth="1.5" />
      <path d="M-3 -2 l11 -7" strokeWidth="1.5" />
    </g>
  );
}

/** Широкий баннер-сцена: остров, море, парусник, пальмы, гирлянда. */
export function SceneBanner() {
  const seaLines = Array.from({ length: 11 }, (_, i) => {
    const y = 208 + i * 8;
    const inset = 40 + i * 26;
    return { y, x1: inset, x2: 1200 - inset - (i % 2) * 70 };
  });

  return (
    <div className="scene" aria-hidden="true">
      <svg viewBox="0 0 1200 300" preserveAspectRatio="xMidYMax meet" role="presentation">
        {/* днём — солнце с лучами */}
        <g className="sun" fill="none" strokeLinecap="round">
          <circle cx="600" cy="96" r="34" strokeWidth="1.6" />
          {Array.from({ length: 12 }, (_, i) => {
            const a = (i * Math.PI) / 6;
            return (
              <path
                key={i}
                d={`M${600 + Math.cos(a) * 44} ${96 + Math.sin(a) * 44} L${600 + Math.cos(a) * 56} ${96 + Math.sin(a) * 56}`}
                strokeWidth="1.4"
                opacity=".7"
              />
            );
          })}
        </g>

        {/* ночью — месяц и звёзды */}
        <g className="moon" fill="none" strokeLinecap="round" strokeLinejoin="round">
          <path
            d="M600 62 A34 34 0 1 0 600 130 A27 27 0 1 1 600 62 Z"
            strokeWidth="1.6"
          />
          {[
            [530, 58],
            [668, 74],
            [648, 34],
            [556, 128],
            [694, 122],
          ].map(([x, y], i) => (
            <path
              key={i}
              className="star"
              d={`M${x} ${y - 7} q1.6 5.4 7 7 q-5.4 1.6 -7 7 q-1.6 -5.4 -7 -7 q5.4 -1.6 7 -7 z`}
              strokeWidth="1.2"
              style={{ animationDelay: `${i * 0.7}s` }}
            />
          ))}
        </g>

        {/* острова на горизонте */}
        <g fill="none" strokeLinejoin="round">
          <path d="M430 196 L488 132 L520 162 L548 138 L604 196 Z" strokeWidth="1.6" />
          <path d="M646 196 L688 152 L730 196 Z" strokeWidth="1.4" opacity=".75" />
          {[
            "M470 178 l16 -18",
            "M492 186 l20 -22",
            "M516 184 l14 -16",
            "M672 184 l12 -14",
          ].map((d) => (
            <path key={d} d={d} strokeWidth="1" opacity=".5" />
          ))}
        </g>

        {/* линия горизонта и штриховка моря */}
        <path d="M0 196 H1200" strokeWidth="1.4" opacity=".8" fill="none" />
        <g strokeLinecap="round" opacity=".62" fill="none">
          {seaLines.map((l) => (
            <path key={l.y} d={`M${l.x1} ${l.y} H${l.x2}`} strokeWidth="1.3" />
          ))}
        </g>

        {/* парусник */}
        <g fill="none" strokeLinejoin="round">
          <path d="M690 190 l0 -40 22 40 z" strokeWidth="1.6" />
          <path d="M686 190 l-16 -30 14 30 z" strokeWidth="1.6" />
          <path d="M666 192 q22 12 46 0 z" strokeWidth="1.6" />
        </g>

        {/* сёрфер на волне */}
        <g transform="translate(392 240)">
          <Surfer />
        </g>

        {/* доски воткнуты в песок слева */}
        <g transform="translate(258 300) rotate(-9)">
          <Surfboard />
        </g>
        <g transform="translate(292 302) rotate(8)">
          <Surfboard stripe={false} />
        </g>

        {/* зонтики на берегу справа */}
        <g fill="none" strokeLinecap="round">
          {[1020, 1094].map((x, i) => (
            <g key={x} transform={`translate(${x} ${222 + i * 10})`}>
              <path d="M-30 0 Q0 -30, 30 0 z" strokeWidth="1.6" />
              <path d="M0 0 v30" strokeWidth="1.6" />
              {[-18, -6, 6, 18].map((dx) => (
                <path key={dx} d={`M${dx} 0 Q${dx / 2} -14, 0 -22`} strokeWidth="0.9" opacity=".55" />
              ))}
            </g>
          ))}
        </g>

        {/* пальмы по краям */}
        <g transform="translate(150 300)">
          <Palm />
        </g>
        <g transform="translate(1058 300)">
          <Palm flip />
        </g>

        {/* гирлянда, натянутая между пальмами */}
        <g transform="translate(196 120)">
          <Garland width={856} />
        </g>
      </svg>
    </div>
  );
}

/** Волна-разделитель между секциями. */
export function WaveRule() {
  return (
    <svg className="wave-rule" viewBox="0 0 360 12" preserveAspectRatio="none" aria-hidden="true">
      <path
        d="M0 6 q15 -6 30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0 t30 0"
        fill="none"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Пальмовый лист: центральная жилка и парные сегменты по всей длине. */
function Frond() {
  const spine = "M22 178 C 60 140, 96 92, 118 26";
  const segments = Array.from({ length: 20 }, (_, i) => {
    const t = (i + 1) / 21;
    // точка на жилке (кубическая Безье вручную, чтобы сегменты садились ровно)
    const bez = (a: number, b: number, c: number, d: number) => {
      const u = 1 - t;
      return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
    };
    const x = bez(22, 60, 96, 118);
    const y = bez(178, 140, 92, 26);
    const len = 46 * Math.sin(Math.PI * t) + 12; // короче у основания и на кончике
    return { x, y, len, t };
  });

  return (
    <g>
      <path d={spine} strokeWidth="2.2" />
      {segments.map((s, i) => (
        <g key={i}>
          <path d={`M${s.x} ${s.y} q ${-s.len * 0.5} ${-s.len * 0.18}, ${-s.len} ${s.len * 0.34}`} strokeWidth="1.4" />
          <path d={`M${s.x} ${s.y} q ${s.len * 0.5} ${s.len * 0.18}, ${s.len * 0.86} ${-s.len * 0.3}`} strokeWidth="1.4" />
        </g>
      ))}
    </g>
  );
}

/** Фоновые листья и штриховка бумаги вместо цветных пятен. */
export function PaperBackdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <svg className="leaf leaf-tl" viewBox="0 0 200 200" fill="none" strokeLinecap="round">
        <Frond />
      </svg>
      <svg className="leaf leaf-br" viewBox="0 0 200 200" fill="none" strokeLinecap="round">
        <Frond />
      </svg>
    </div>
  );
}
