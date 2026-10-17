import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";

export const metadata = {
  title: "revora — evidence that changes the next decision",
  description: "explore verified agent memory: measured incident recovery, graph-backed handoff, and paired memory ablations with FalkorDB.",
};

export const viewport = {
  themeColor: "#070a0f",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
