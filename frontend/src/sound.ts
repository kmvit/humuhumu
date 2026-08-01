// Короткий сигнал «дин-дон» через Web Audio — без внешних файлов.
// Браузеры блокируют звук до первого действия пользователя, поэтому
// возобновляем аудиоконтекст на первый клик/нажатие клавиши.
let ctx: AudioContext | null = null;

function getCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!ctx) {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
  }
  return ctx;
}

if (typeof window !== "undefined") {
  const resume = () => {
    const c = getCtx();
    if (c && c.state === "suspended") c.resume();
    window.removeEventListener("pointerdown", resume);
    window.removeEventListener("keydown", resume);
  };
  window.addEventListener("pointerdown", resume);
  window.addEventListener("keydown", resume);
}

export function playChime() {
  const c = getCtx();
  if (!c) return;
  if (c.state === "suspended") c.resume();
  const now = c.currentTime;
  const notes = [880, 1174.66]; // A5 → D6
  notes.forEach((freq, i) => {
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const t = now + i * 0.14;
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(0.25, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.35);
    osc.connect(gain).connect(c.destination);
    osc.start(t);
    osc.stop(t + 0.36);
  });
}
