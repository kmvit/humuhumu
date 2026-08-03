// Набор SVG-иконок (контурные, единый stroke 2px). Без эмодзи.
type Props = { name: IconName; size?: number };

const paths: Record<string, string> = {
  coffee:
    "M10 2v2M14 2v2M16 8a4 4 0 0 1 0 8h-1M4 8h14v5a6 6 0 0 1-6 6H8a6 6 0 0 1-6-6V8h2zM2 20h16",
  wallet:
    "M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2M3 7h18a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-4a3 3 0 0 1 0-6h5",
  receipt:
    "M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1-2-1zM8 7h8M8 11h8M8 15h5",
  user: "M20 21a8 8 0 0 0-16 0M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  store:
    "M3 9l1.5-5.5A1 1 0 0 1 5.5 3h13a1 1 0 0 1 1 .5L21 9M3 9v11a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V9M3 9a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0",
  chart: "M3 3v18h18M7 16v-5M12 16V8M17 16v-9",
  laptop: "M4 5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v11H4V5zM2 19a1 1 0 0 1 1-1h18a1 1 0 0 1 1 1 1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  check: "M20 6L9 17l-5-5",
  download: "M12 3v12M8 11l4 4 4-4M4 21h16",
  share: "M12 16V4M8 8l4-4 4 4M4 12v6a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6",
  arrowUp: "M12 19V5M5 12l7-7 7 7",
  arrowDown: "M12 5v14M19 12l-7 7-7-7",
  gift: "M20 12v10H4V12M2 7h20v5H2zM12 22V7M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7zM12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z",
  sandwich:
    "M3 11h18M4 11l1-3a2 2 0 0 1 2-1.3h10A2 2 0 0 1 19 8l1 3M3 15h18M5 15v1a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-1",
  bowl: "M2 11h20a10 9 0 0 1-20 0zM5 7l1 4M19 7l-1 4M12 5v6",
  iceCream: "M7 11a5 5 0 0 1 10 0M12 11v0M8 11l4 10 4-10M9.5 15h5",
  spark: "M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z",
  palm: "M13 8c4-3 7-1 8 1-3-1-5 0-6 1 3-1 5 1 5 4-2-2-4-2-6-1 2 1 3 3 2 5-1-2-3-3-5-3 1 2 1 4-1 5 0-3-1-4-2-5-2 2-5 2-7 1 2-1 3-3 3-5-2 1-4 1-6-1 3 0 5-1 6-3-3 0-5-2-5-4 2 1 4 1 6 0M13 8l-1 13",
  leaf: "M11 20A7 7 0 0 1 4 13c0-5 4-9 16-9 0 12-7 16-11 16zM4 21c3-8 7-11 14-13",
  wave: "M2 8c2 0 2 2 4 2s2-2 4-2 2 2 4 2 2-2 4-2 2 2 4 2M2 14c2 0 2 2 4 2s2-2 4-2 2 2 4 2 2-2 4-2 2 2 4 2",
  flower: "M12 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 8c0-3-1-5-3-5s-2 3 0 5M12 8c0-3 1-5 3-5s2 3 0 5M14.5 11c3-1 5-1 5.5 1s-2 3-4 1M9.5 11c-3-1-5-1-5.5 1s2 3 4 1M11 13.5c-1 3-1 5 1 5.5s3-2 1-4M13 13.5c1 3 1 5-1 5.5",
};

export type IconName = keyof typeof paths;

export default function Icon({ name, size = 20 }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

// Иконка категории по названию
export function categoryIcon(name: string): IconName {
  const n = name.toLowerCase();
  if (n.includes("коф") || n.includes("напит")) return "coffee";
  if (n.includes("сэнд") || n.includes("бургер")) return "sandwich";
  if (n.includes("боул") || n.includes("салат")) return "bowl";
  if (n.includes("морож") || n.includes("десерт")) return "iceCream";
  return "spark";
}
