import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Poker Analysis — 德州扑克进阶训练",
  description: "面向进阶牌手的 GTO 决策训练与剥削分析平台",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
