import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import "./staff.css";
import "./audit.css";
import "./nextgen.css";
import "./theme.css";

export const metadata: Metadata = {
  title: "NovaCart Support",
  icons: { icon: "/novacart-mark.svg" },
  description: "Secure AI-assisted customer care for NovaCart",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
