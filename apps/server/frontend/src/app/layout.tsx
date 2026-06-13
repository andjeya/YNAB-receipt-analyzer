import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Hanken_Grotesk } from "next/font/google";
import type { ReactNode } from "react";

import { Providers } from "@/components/providers";

import "./globals.css";

// Display face — Bricolage Grotesque: a friendly, slightly quirky humanist
// grotesque that carries warmth + polish (the Snappy/Duolingo register)
// without reading as a generic system default.
const headingFont = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
  variable: "--font-heading",
});

// Body face — Hanken Grotesk: clean, gently rounded, highly legible at the
// small sizes the dense receipt lists lean on.
const bodyFont = Hanken_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-body",
});

export const metadata: Metadata = {
  title: "Snappy · Receipts → YNAB",
  description: "Local receipt review and YNAB sync",
};

export const viewport: Viewport = {
  viewportFit: "cover",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={`${headingFont.variable} ${bodyFont.variable}`}>
      <body className="font-body text-ink antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
