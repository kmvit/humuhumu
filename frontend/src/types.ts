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

export type Role = "client" | "waiter" | "cook" | "bar" | "admin";

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
  is_available: boolean;
}

export interface OrderItem {
  id: number;
  product: number;
  product_name: string;
  station: Station;
  status: StationStatus;
  quantity: number;
  unit_price: string;
  subtotal: string;
}

export type OrderStatus = "open" | "paid" | "cancelled";

export interface Order {
  id: number;
  client: number | null;
  waiter: number | null;
  closed_by: number | null;
  table: string;
  status: OrderStatus;
  status_display: string;
  food_status: StationStatus;
  drinks_status: StationStatus;
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

export interface TokenPackage {
  id: number;
  pay_amount: string;
  bonus_amount: string;
  total_tokens: string;
  is_active: boolean;
}
