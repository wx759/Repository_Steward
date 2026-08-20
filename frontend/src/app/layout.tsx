import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Repository Steward",
  description: "A small, extensible coding agent core."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        {children}
      </body>
    </html>
  );
}
