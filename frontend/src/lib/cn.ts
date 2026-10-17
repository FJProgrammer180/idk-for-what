import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** clsx for conditional classes, tailwind-merge so later utilities actually win. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
