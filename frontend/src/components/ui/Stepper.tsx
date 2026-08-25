import Icon, { type IconName } from "../Icon";

// Единый контрол количества «− N +» для всего приложения (меню, сборка
// заказа, склад, модалка стола). Презентационный: логику шага держит
// вызывающий через onDec/onInc. decDanger подсвечивает «−» красным
// (например, когда следующий шаг удалит позицию).
export default function Stepper({
  value,
  onDec,
  onInc,
  disabled = false,
  decDisabled = false,
  decDanger = false,
  decIcon = "minus",
  size = 16,
  width,
  ariaDec = "Меньше",
  ariaInc = "Больше",
}: {
  value: number | string;
  onDec: () => void;
  onInc: () => void;
  disabled?: boolean;
  decDisabled?: boolean; // отключить только «−» (например, позицию уже готовят)
  decDanger?: boolean;
  decIcon?: IconName;
  size?: number;
  width?: number;
  ariaDec?: string;
  ariaInc?: string;
}) {
  return (
    <div className="stepper" style={width ? { width } : undefined}>
      <button
        className={decDanger ? "danger" : ""}
        onClick={onDec}
        disabled={disabled || decDisabled}
        aria-label={ariaDec}
      >
        <Icon name={decIcon} size={size} />
      </button>
      <span className="count num">{value}</span>
      <button onClick={onInc} disabled={disabled} aria-label={ariaInc}>
        <Icon name="plus" size={size} />
      </button>
    </div>
  );
}
