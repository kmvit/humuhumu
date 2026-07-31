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

export type Role = "client" | "cashier" | "admin";

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
  price: string;
  weight_grams: number | null;
  is_available: boolean;
}

export interface OrderItem {
  id: number;
  product: number;
  product_name: string;
  quantity: number;
  unit_price: string;
  subtotal: string;
}

export type OrderStatus =
  | "pending"
  | "paid"
  | "preparing"
  | "ready"
  | "done"
  | "cancelled";

export interface Order {
  id: number;
  client: number;
  cashier: number | null;
  status: OrderStatus;
  status_display: string;
  pay_method: "card" | "tokens";
  total: string;
  items: OrderItem[];
  created_at: string;
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
