export type AppTheme = "neutral" | "warm" | "strict" | "humu";

export interface Site {
  name: string;
  tagline: string;
  logo: string | null;
  phone: string;
  email: string;
  address: string;
  working_hours: string;
  instagram: string;
  telegram: string;
  about: string;
  theme: AppTheme;
  accent_color: string;
}

export type Role = "client" | "waiter" | "cook" | "bar" | "warehouse" | "admin";

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

export interface Product {
  id: number;
  category: number;
  category_name: string;
  name: string;
  description: string;
  image: string | null;
  thumbnail: string | null;
  price: string;
  weight_grams: number | null;
  prep_minutes: number | null;
  is_available: boolean;
  is_stopped: boolean;
  likes?: number;
}

export interface OrderItem {
  id: number;
  product: number;
  product_name: string;
  station: Station;
  status: StationStatus;
  guest: number | null;
  quantity: number;
  unit_price: string;
  subtotal: string;
}

export type OrderStatus = "requested" | "open" | "awaiting" | "paid" | "cancelled";

export type PayMethod = "cash" | "card";

export interface Order {
  id: number;
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

export interface TokenPackage {
  id: number;
  pay_amount: string;
  bonus_amount: string;
  total_tokens: string;
  is_active: boolean;
}
