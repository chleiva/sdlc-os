import { formatCurrency } from "./formatter";

export function renderTotal(amount: number): string {
  return formatCurrency(amount);
}
