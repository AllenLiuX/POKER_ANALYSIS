import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并 className（clsx 组合 + tailwind-merge 去冲突），shadcn 同款约定。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
