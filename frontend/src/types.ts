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

export type OrderStatus = "requested" | "open" | "paid" | "cancelled";

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

export interface StockItem {
  id: number;
  category: number;
  category_name: string;
  name: string;
  unit: StockUnit;
  unit_display: string;
  quantity: string;
  min_quantity: string | null;
  is_low: boolean;
  is_active: boolean;
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

export interface TokenPackage {
  id: number;
  pay_amount: string;
  bonus_amount: string;
  total_tokens: string;
  is_active: boolean;
}
