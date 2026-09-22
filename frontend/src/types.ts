export type AppTheme = "neutral" | "warm" | "strict" | "island" | "padacha";

/** Тариф «Падачи» — это формат заведения: стойка дешевле зала. */
export type Plan = "counter" | "hall";

/** Фичи тарифа — по ним фронт прячет разделы; настоящий запрет на бэке. */
export type Feature = "stations" | "inventory" | "shifts" | "finance" | "loyalty";

export interface Site {
  name: string;
  tagline: string;
  app_short_name: string;
  logo: string | null;
  phone: string;
  email: string;
  address: string;
  working_hours: string;
  instagram: string;
  telegram: string;
  about: string;
  /** Зал с официантами или стойка с выдачей по номеру. */
  service_mode: "hall" | "counter";
  theme: AppTheme;
  /** Какой режим видит гость, пока сам не переключил. */
  dark_by_default: boolean;
  /** Гостю показывать кнопку оплаты: банк подключён И владелец не выключил. */
  online_payment: boolean;
  /** Выключатель владельца — сам по себе оплату не включает без доступов. */
  online_payment_on: boolean;
  accent_color: string;
  // Бонусная программа (тариф «Максимум»). 1 бонус = 1 ₽.
  bonus_enabled: boolean;
  bonus_welcome: number;
  bonus_earn_percent: string;
  /** Официант списывает бонусы на закрытии счёта. */
  bonus_redeem_waiter: boolean;
  /** Гость списывает бонусы сам в приложении. */
  bonus_redeem_guest: boolean;
  // Реквизиты продавца — подставляются в оферту, оплату и контакты.
  merchant_type: string;
  merchant_name: string;
  merchant_short: string;
  merchant_address: string;
  merchant_inn: string;
  merchant_ogrn: string;
  merchant_account: string;
  merchant_bank: string;
  merchant_bank_inn: string;
  merchant_bik: string;
  merchant_corr_account: string;
  merchant_bank_address: string;
  acquirer: string;
  legal_updated: string;
  /** Тариф заведения по сетке «Падачи» и что в него входит. */
  plan: Plan;
  features: Feature[];
}

export type Role = "client" | "waiter" | "cook" | "bar" | "warehouse" | "admin";

/** Сотрудник заведения — раздел «Сотрудники» в панели владельца. */
export interface StaffMember {
  id: number;
  username: string;
  name: string;
  role: Role;
  role_display: string;
  phone: string | null;
  is_active: boolean;
}

/** Статус подписки «Падачи» — для баннеров персонала (LicenseGuard). */
export interface LicenseInfo {
  enabled: boolean;
  status: "active" | "expiring" | "grace" | "blocked";
  /** Своя точка «Падачи»: подписка не тарифицируется, дата не показывается. */
  internal: boolean;
  plan: Plan;
  paid_until: string | null;
  grace_days: number;
  checked_at: string | null;
}

/** Сколько распознаваний чеков осталось в этом месяце (лимит тарифа). */
export interface ScanQuota {
  used: number;
  limit: number;
  left: number;
}

export type Station = "kitchen" | "bar";

export type StationStatus = "new" | "in_progress" | "ready";

export interface Me {
  id: number;
  username: string;
  phone: string | null;
  role: Role;
  balance: string | null;
}

export interface Category {
  id: number;
  name: string;
  icon: string | null;
  station: Station;
  sort_order: number;
  is_active: boolean;
}

export interface Table {
  id: number;
  name: string;
  sort_order: number;
  is_active: boolean;
}

/** Вариант товара — то, что продаётся: объём со своей ценой и тех картой. */
export interface ProductVariant {
  id: number;
  /** «0,33 л»; пусто у единственного варианта — тогда размер не показываем. */
  label: string;
  price: string;
  weight_grams: number | null;
  prep_minutes: number | null;
  is_stopped: boolean;
  is_active: boolean;
  sort_order: number;
}

/** Опция внутри набора: «Овсяное», «+ шот», «Без сиропа». */
export interface Modifier {
  id: number;
  name: string;
  /** Надбавка к цене варианта; может быть нулевой и отрицательной. */
  price_delta: string;
  is_stopped: boolean;
  sort_order: number;
}

/** Набор опций к блюду. Опции набираются ПОВЕРХ выбранного объёма. */
export interface ModifierGroup {
  id: number;
  name: string;
  min_choices: number;
  /** 1 — выбрать одно из набора; больше — можно набрать несколько. */
  max_choices: number;
  is_required: boolean;
  sort_order: number;
  modifiers: Modifier[];
}

export interface Product {
  id: number;
  category: number;
  category_name: string;
  name: string;
  description: string;
  image: string | null;
  thumbnail: string | null;
  /** Фото нарисовано нейросетью — в меню рядом с ним стоит «иллюстрация». */
  image_is_generated: boolean;
  is_available: boolean;
  sort_order: number;
  /** Минимум один. Гостю приходят только продающиеся. */
  variants: ProductVariant[];
  /** Наборы опций блюда; пусто — выбирать нечего. */
  modifier_groups: ModifierGroup[];
  likes?: number;
}

/** Показывать выбор размера, только если вариантов правда несколько. */
export function hasSizes(p: Product): boolean {
  return p.variants.length > 1;
}

/** Самый дешёвый вариант — цена «от» в карточке и выбор по умолчанию. */
export function baseVariant(p: Product): ProductVariant | undefined {
  return p.variants[0];
}

/** Выбранная опция в позиции — снимком на момент продажи. */
export interface OrderItemModifier {
  id: number;
  modifier: number;
  name: string;
  price_delta: string;
}

export interface OrderItem {
  id: number;
  /** Что продано. Цена уже зафиксирована в unit_price. */
  variant: number;
  /** «0,33 л» — пусто, если у товара один вариант. */
  variant_label: string;
  /** id карточки меню — для группировок на экранах. */
  product: number;
  /** Полное имя с вариантом: «Кис-кис 0,33 л». */
  product_name: string;
  station: Station;
  status: StationStatus;
  guest: number | null;
  quantity: number;
  /** Цена варианта без надбавок; в subtotal опции уже учтены. */
  unit_price: string;
  subtotal: string;
  modifiers: OrderItemModifier[];
  /** «овсяное, без сиропа» — готовая строка для досок и чека. */
  options_text: string;
}

export type OrderStatus = "requested" | "open" | "awaiting" | "paid" | "cancelled";

export type PayMethod = "cash" | "card";

export interface Order {
  id: number;
  /** Номер для выдачи, обнуляется каждый день. */
  daily_number: number | null;
  client: number | null;
  waiter: number | null;
  closed_by: number | null;
  table: string;
  comment: string;
  customer_name: string;
  public_token: string | null;
  status: OrderStatus;
  status_display: string;
  pay_method: PayMethod;
  pay_method_display: string;
  fiscal_receipt: string;
  food_status: StationStatus;
  drinks_status: StationStatus;
  food_served: boolean;
  drinks_served: boolean;
  has_food: boolean;
  has_drinks: boolean;
  is_ready: boolean;
  total: string;
  /** Списано бонусами (1 бонус = 1 ₽). */
  bonus_spent: string;
  /** Сколько гость платит деньгами: total за вычетом бонусов. */
  payable: string;
  items: OrderItem[];
  created_at: string;
  food_started_at: string | null;
  food_ready_at: string | null;
  drinks_started_at: string | null;
  drinks_ready_at: string | null;
  food_served_at: string | null;
  drinks_served_at: string | null;
  closed_at: string | null;
}

export interface Wallet {
  id: number;
  balance: string;
}

export interface TokenTransaction {
  id: number;
  type: string;
  type_display: string;
  amount: string;
  balance_after: string;
  order: number | null;
  comment: string;
  created_at: string;
}

export type StockUnit = "g" | "ml" | "pcs";

export interface StockCategory {
  id: number;
  name: string;
  sort_order: number;
  is_active: boolean;
  /** Сколько товаров внутри: непустую категорию удалить нельзя. */
  items_count: number;
}

/** Вариант товара: как его называют при закупке и пишут в чеках. */
export interface StockAlias {
  id: number;
  name: string;
}

/** Товар склада — «Креветки», «Кола»: один остаток независимо от марки. */
export interface StockItem {
  id: number;
  category: number;
  category_name: string;
  name: string;
  unit: StockUnit;
  unit_display: string;
  quantity: string;
  min_quantity: string | null;
  /** До какого остатка закупаем; пусто — два порога. */
  target_quantity: string | null;
  /** Сколько не хватает до цели — столько уйдёт в закуп. */
  shortage: string;
  is_low: boolean;
  is_active: boolean;
  aliases: StockAlias[];
}

/** Строка тех карты: расход товара на одну порцию блюда. */
export interface RecipeLine {
  id: number;
  item: number;
  item_name: string;
  unit_display: string;
  quantity: string;
  comment: string;
}

/** Тех карта блюда: состав и себестоимость по последним закупкам. */
export interface Recipe {
  /** Карта принадлежит варианту: у каждого объёма состав свой. */
  variant: number;
  /** id карточки меню — объёмы одного блюда группируются по нему. */
  product: number;
  product_name: string;
  category_name: string;
  price: string;
  lines: RecipeLine[];
  cost: string;
  /** Цена известна не по всем ингредиентам — себестоимость неполная. */
  cost_partial: boolean;
}

export interface PurchaseLine {
  id: number;
  purchase: number;
  item: number;
  item_name: string;
  unit_display: string;
  category_name: string;
  in_stock: string;
  quantity: string;
  /** Строку предложила программа (не кладовщик). */
  is_auto: boolean;
  is_done: boolean;
  comment: string;
}

export interface PurchaseList {
  id: number;
  date: string;
  lines: PurchaseLine[];
  created_at: string;
}

export interface ReceiptItem {
  id: number;
  item: number;
  item_name: string;
  unit: StockUnit;
  unit_display: string;
  quantity: string;
  unit_cost: string | null;
  subtotal: string | null;
}

export interface Receipt {
  id: number;
  received_by: number | null;
  received_by_name: string;
  supplier: string;
  comment: string;
  total_cost: string;
  items: ReceiptItem[];
  created_at: string;
}

// Оприходование по фото чека
export type ReceiptScanStatus = "pending" | "parsed" | "failed" | "confirmed";

export interface ReceiptScanLine {
  raw_name: string;
  raw_quantity: number | null;
  raw_unit: string;
  unit_cost: number | null;
  matched_item_id: number | null;
  matched_item_name: string | null;
  matched_item_unit: StockUnit | null;
  base_quantity: number | null;
  unit_ok: boolean;
  confidence: number;
}

export interface ReceiptScanParsed {
  supplier: string;
  date: string | null;
  total: number | null;
  lines: ReceiptScanLine[];
}

export interface ReceiptScan {
  id: number;
  image: string;
  status: ReceiptScanStatus;
  status_display: string;
  parsed: ReceiptScanParsed | null;
  error: string;
  receipt: number | null;
  created_at: string;
  updated_at: string;
}

/** Работник в смене. */
export interface ShiftMember {
  id: number;
  user: number;
  name: string;
  role: Role;
  role_display: string;
  added_at: string;
  /** К выплате этому человеку за смену. */
  payout: string;
}

/** Сотрудник, которого менеджер может поставить в смену. */
export interface StaffUser {
  id: number;
  name: string;
  role: Role;
  role_display: string;
}

/** Смена (рабочий день): состав, выручка и расчёт оплаты. */
export interface Shift {
  id: number | null;
  date: string;
  daily_rate: string;
  bonus_percent: string;
  /** Стол списаний — заказы с него вычитаются из оплаты. */
  penalty_table: string;
  revenue: string;
  penalty: string;
  /** Ручной штраф за смену (задаёт менеджер), делится на всех. */
  manual_penalty: string;
  bonus_pool: string;
  members_count: number;
  /** Доли на одного человека в смене. */
  bonus_share: string;
  penalty_share: string;
  manual_penalty_share: string;
  payout: string;
  members: ShiftMember[];
  /** Только в /shifts/day/: я в этой смене. */
  in_shift?: boolean;
  /** Только в /shifts/day/: можно менять состав (менеджер или админ). */
  can_edit?: boolean;
  /** Только в /shifts/month/: я был в этой смене. */
  mine?: boolean;
}

/** Строка сводки «к выплате» за период. */
export interface PayrollRow {
  user: number;
  name: string;
  role: Role;
  role_display: string;
  days: number;
  base: string;
  bonus: string;
  penalty: string;
  total: string;
}

export interface Payroll {
  from: string;
  to: string;
  rows: PayrollRow[];
}

// ——— Финансы: ведомость по зарплате за месяц ———

/** Строка ведомости: начислено из смен, выплачено — из журнала выплат. */
export interface StatementRow extends PayrollRow {
  accrued: string;
  paid: string;
  left: string;
  settled: boolean;
}

export interface StatementTotals {
  accrued: string;
  paid: string;
  left: string;
  people: number;
  shifts: number;
}

export interface Statement {
  period: string;
  from: string;
  to: string;
  rows: StatementRow[];
  totals: StatementTotals;
}

/** Отчёт о прибыли за месяц. */
export interface ProfitReport {
  period: string;
  from: string;
  to: string;
  revenue: string;
  checks: number;
  avg_check: string;
  cash: string;
  card: string;
  cogs: string;
  gross: string;
  margin: number;
  payroll: string;
  expenses: string;
  profit: string;
  /** Закуп продуктов — справочно, в прибыли не участвует. */
  purchases: string;
  /** Доля выручки, у которой известна себестоимость, %. */
  cost_coverage: number;
  /** Пока себестоимость неполная, прибыль — оценка сверху, а не факт. */
  is_estimate: boolean;
}

/** Статья расходов заведения: аренда, коммуналка и прочее. */
export interface ExpenseCategory {
  id: number;
  name: string;
  sort_order: number;
  is_active: boolean;
}

export interface Expense {
  id: number;
  date: string;
  category: number;
  category_name: string;
  amount: string;
  comment: string;
}

export interface Expenses {
  period: string;
  from: string;
  to: string;
  rows: Expense[];
  by_category: { category: number; name: string; total: string }[];
  total: string;
}

/** Расшифровка: из чего сложилась сумма работника в конкретный день. */
export interface StatementDay {
  date: string;
  revenue: string;
  members_count: number;
  daily_rate: string;
  bonus_share: string;
  penalty_share: string;
  manual_penalty_share: string;
  payout: string;
}

export interface TokenPackage {
  id: number;
  pay_amount: string;
  bonus_amount: string;
  total_tokens: string;
  is_active: boolean;
}

/** Участник бонусной программы: 1 бонус = 1 ₽. */
export interface LoyaltyMember {
  id: number;
  name: string;
  phone: string;
  birth_date: string | null;
  balance: string;
  created_at: string;
}

// ——— фото блюд нейросетью ———

/** Образец посуды: в нём нейросеть рисует блюда заведения. */
export interface DishwareSample {
  id: number;
  name: string;
  image: string;
  note: string;
  category: number | null;
  sort_order: number;
  is_active: boolean;
}

export type PhotoStyle = "studio" | "counter" | "wood" | "dark";

/** Стиль фото меню — один на заведение, чтобы карточки выглядели как серия. */
export interface MenuImageSettings {
  id: number;
  style: PhotoStyle;
  extra_prompt: string;
  aspect_ratio: string;
  background: string | null;
}

export interface ImageGeneration {
  id: number;
  batch: number | null;
  product: number;
  product_name: string;
  prompt: string;
  model: string;
  image: string | null;
  status: "pending" | "ready" | "failed";
  error: string;
  cost_usd: string;
  /** Когда эту картинку поставили в меню; у блюда такая одна. */
  applied_at: string | null;
  created_at: string;
}

export interface ImageBatch {
  id: number;
  category: number | null;
  created_at: string;
  counts: { total: number; pending: number; ready: number; failed: number };
  generations: ImageGeneration[];
}

/** Остаток генераций в месяце и потраченное на них. */
export interface ImageQuota {
  used: number;
  limit: number;
  left: number;
  spent_usd: string;
}
